"""Hermes-managed local llama-server runtime lifecycle.

This module deliberately stays at the CLI edge.  Inference continues to use
Hermes' existing OpenAI-compatible ``custom`` provider.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform as host_platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from hermes_cli._subprocess_compat import windows_detach_popen_kwargs
from hermes_constants import get_config_path, get_hermes_home


class LocalRuntimeError(RuntimeError):
    """A local-runtime operation could not be completed safely."""


@dataclass(frozen=True)
class PullResult:
    state: str
    files: tuple[Path, ...]


@dataclass(frozen=True)
class RuntimePullResult:
    state: str
    server: Path


@dataclass(frozen=True)
class LocalStatus:
    state: str
    pid: int | None = None
    port: int | None = None
    model_id: str | None = None
    backend: str | None = None
    base_url: str | None = None
    stdout_log: Path | None = None
    stderr_log: Path | None = None
    error: str | None = None


def load_builtin_manifest() -> dict[str, Any]:
    """Return an isolated copy of the pinned local-runtime manifest."""
    from hermes_cli.local_runtime_manifest import BUILTIN_LOCAL_MANIFEST

    return copy.deepcopy(BUILTIN_LOCAL_MANIFEST)


def configure_local_provider(*, model_id: str, base_url: str, context_length: int) -> None:
    """Persist the running sidecar as the existing custom provider shape."""
    from hermes_cli.config import atomic_config_write, read_raw_config

    config = read_raw_config()
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
    providers["hermes-local"] = {
        "name": "Hermes Local",
        "api": base_url,
        "transport": "chat_completions",
        "default_model": model_id,
        "models": {model_id: {"context_length": context_length}},
        "discover_models": False,
    }
    atomic_config_write(get_config_path(), config, sort_keys=False)


def _server_candidate(root: Path, target_platform: str) -> Path | None:
    name = "llama-server.exe" if target_platform == "win32" else "llama-server"
    return next((path for path in root.rglob(name) if path.is_file()), None) if root.is_dir() else None


def is_runtime_cache_complete(root: Path, *, backend: str, target_platform: str) -> bool:
    """Validate the side-by-side loader files required by a cached runtime."""
    server = _server_candidate(root, target_platform)
    if server is None:
        return False
    siblings = tuple(path for path in server.parent.iterdir() if path.is_file())
    if target_platform == "win32":
        dll_names = {path.name.lower() for path in siblings if path.suffix.lower() == ".dll"}
        if not dll_names:
            return False
        if backend == "cuda":
            return "ggml-cuda.dll" in dll_names and any(
                name.startswith(("cudart", "cublas")) for name in dll_names
            )
        return True
    return any(path.suffix.lower() in {".so", ".dylib"} for path in siblings)


class LocalRuntimeManager:
    """Manage assets and process state below ``$HERMES_HOME/local-runtime``."""

    def __init__(self, *, manifest: dict[str, Any]) -> None:
        if manifest.get("schema") != "hermes-local-manifest@1":
            raise LocalRuntimeError("unsupported local runtime manifest schema")
        self.manifest = manifest
        self.root = get_hermes_home() / "local-runtime"

    def pull_model(self, model_id: str, quant: str | None = None) -> PullResult:
        model = self._model(model_id)
        releases = model.get("releases") or []
        release = next(
            (item for item in releases if quant is None or item.get("quant") == quant),
            None,
        )
        if not isinstance(release, dict):
            raise LocalRuntimeError(f"model {model_id!r} has no matching release")

        quant_name = str(release.get("quant") or "").strip()
        urls = release.get("urls") or []
        checksums = release.get("sha256") or []
        if not quant_name or not isinstance(urls, list) or len(urls) != len(checksums) or not urls:
            raise LocalRuntimeError(f"model {model_id!r} has an invalid release manifest")

        target = self.root / "models" / model_id / quant_name
        target.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for index, (url, checksum) in enumerate(zip(urls, checksums, strict=True), 1):
            name = Path(urllib.parse.urlparse(str(url)).path).name or f"part-{index}.gguf"
            destination = target / name
            self._download_verified(str(url), destination, str(checksum))
            files.append(destination)

        license_url = str(model.get("license_file") or "").strip()
        license_checksum = str(model.get("license_file_sha256") or "").strip()
        if not license_url or not license_checksum:
            raise LocalRuntimeError(f"model {model_id!r} has no verifiable license file")
        self._download_verified(license_url, target / "LICENSE", license_checksum)

        expected_size = release.get("size_bytes")
        if isinstance(expected_size, int) and sum(path.stat().st_size for path in files) != expected_size:
            raise LocalRuntimeError(f"downloaded size for {model_id!r} does not match manifest")

        marker = {
            "schema": self.manifest["schema"],
            "model_id": model_id,
            "quant": quant_name,
            "files": [path.name for path in files],
        }
        self._atomic_json(target / "complete.json", marker)
        return PullResult(state="ready", files=tuple(files))

    def pull_runtime(self, backend: str) -> RuntimePullResult:
        with self._operation_lock():
            return self._pull_runtime_unlocked(backend)

    def _pull_runtime_unlocked(self, backend: str) -> RuntimePullResult:
        machine = host_platform.machine().lower()
        runtime = next(
            (
                item
                for item in self.manifest.get("runtimes") or []
                if isinstance(item, dict)
                and item.get("platform") == sys.platform
                and str(item.get("machine", "")).lower() == machine
                and item.get("backend") == backend
            ),
            None,
        )
        if runtime is None:
            raise LocalRuntimeError(
                f"no {backend} llama-server runtime for {sys.platform}/{machine}"
            )
        version = str(runtime.get("version") or "").strip()
        assets = runtime.get("assets") or []
        if not version or not isinstance(assets, list) or not assets:
            raise LocalRuntimeError("invalid llama-server runtime manifest")

        target = self.root / "bin" / f"{version}-{backend}"
        cached = self._find_server(target, backend=backend)
        if cached is not None:
            return RuntimePullResult(state="ready", server=cached)

        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.extracting-", dir=target.parent))
        try:
            for asset in assets:
                if not isinstance(asset, dict):
                    raise LocalRuntimeError("invalid runtime asset manifest")
                url = str(asset.get("url") or "")
                checksum = str(asset.get("sha256") or "")
                name = Path(urllib.parse.urlparse(url).path).name
                if not url or not checksum or not name:
                    raise LocalRuntimeError("invalid runtime asset manifest")
                archive = self.root / "downloads" / version / name
                self._download_verified(url, archive, checksum)
                self._extract_archive(archive, staging)
            server = self._find_server(staging, backend=backend)
            if server is None:
                raise LocalRuntimeError("runtime archive does not contain a complete llama-server")
            staging.replace(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        server = self._find_server(target, backend=backend)
        if server is None:  # pragma: no cover - rename cannot remove extracted files
            raise LocalRuntimeError("extracted llama-server cache is incomplete")
        return RuntimePullResult(state="ready", server=server)

    def start(
        self,
        model_id: str,
        *,
        backend: str = "vulkan",
        port: int = 8080,
        timeout: float = 120.0,
    ) -> LocalStatus:
        with self._operation_lock():
            current = self.status()
            if current.state in {"starting", "ready"}:
                if current.model_id == model_id and current.port == port:
                    return current
                raise LocalRuntimeError("a Hermes local runtime is already running")
            self._assert_port_available(port)
            runtime = self._pull_runtime_unlocked(backend)
            model_path = self._complete_model_path(model_id)
            logs = self.root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            stdout_log = logs / "llama-server.stdout.log"
            stderr_log = logs / "llama-server.stderr.log"
            if stdout_log == stderr_log:  # launch invariant, especially on Windows
                raise LocalRuntimeError("stdout and stderr log paths must be different")

            state: dict[str, Any] = {
                "state": "starting",
                "model_id": model_id,
                "backend": backend,
                "port": port,
                "server": str(runtime.server.resolve()),
                "stdout_log": str(stdout_log),
                "stderr_log": str(stderr_log),
            }
            self._write_state(state)
            command = [
                str(runtime.server),
                "--host", "127.0.0.1",
                "--port", str(port),
                "--model", str(model_path),
                "--alias", model_id,
                "--jinja",
            ]
            if backend in {"vulkan", "cuda"}:
                command.extend(["--n-gpu-layers", "99"])
            env = os.environ.copy()
            if sys.platform != "win32":
                loader_key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
                previous = env.get(loader_key, "")
                env[loader_key] = str(runtime.server.parent) + (os.pathsep + previous if previous else "")

            process: subprocess.Popen[bytes] | None = None
            try:
                with stdout_log.open("ab", buffering=0) as stdout, stderr_log.open(
                    "ab", buffering=0
                ) as stderr:
                    process = subprocess.Popen(
                        command,
                        cwd=runtime.server.parent,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        shell=False,
                        **windows_detach_popen_kwargs(),
                    )
                created = psutil.Process(process.pid).create_time()
                state.update({"pid": process.pid, "process_create_time": created})
                self._write_state(state)
                (self.root / "llama-server.pid").write_text(str(process.pid), encoding="utf-8")
                (self.root / "port").write_text(str(port), encoding="utf-8")
                self._atomic_json(self.root / "llama-server.lock", {
                    "pid": process.pid,
                    "process_create_time": created,
                    "server": str(runtime.server.resolve()),
                })
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise LocalRuntimeError(
                            f"llama-server exited during startup with code {process.returncode}"
                        )
                    if self._health_ready(port):
                        state["state"] = "ready"
                        self._write_state(state)
                        return self._status_from_state(state)
                    time.sleep(0.05)
                raise LocalRuntimeError(f"llama-server did not become ready within {timeout:g}s")
            except Exception as exc:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                state["state"] = "error"
                state["error"] = str(exc)
                self._write_state(state)
                self._remove_live_markers()
                if isinstance(exc, LocalRuntimeError):
                    raise
                raise LocalRuntimeError(f"could not start llama-server: {exc}") from exc

    def status(self) -> LocalStatus:
        state_path = self.root / "state.json"
        if not state_path.is_file():
            return LocalStatus(state="stopped")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return LocalStatus(state="error", error="local runtime state is unreadable")
        if state.get("state") in {"starting", "ready"} and not self._is_managed_process(state):
            state["state"] = "error"
            state["error"] = "managed llama-server process is no longer running"
            self._write_state(state)
            self._remove_live_markers()
        elif state.get("state") == "ready" and not self._health_ready(int(state["port"])):
            state["state"] = "error"
            state["error"] = "managed llama-server failed its health checks"
            self._write_state(state)
        return self._status_from_state(state)

    def stop(self, *, timeout: float = 10.0) -> LocalStatus:
        with self._operation_lock():
            state_path = self.root / "state.json"
            if not state_path.is_file():
                self._remove_live_markers()
                return LocalStatus(state="stopped")
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                state = {}
            pid = state.get("pid")
            if isinstance(pid, int) and psutil.pid_exists(pid):
                if not self._is_managed_process(state):
                    raise LocalRuntimeError("refusing to stop a process that is not the managed llama-server")
                process = psutil.Process(pid)
                process.terminate()
                try:
                    process.wait(timeout=timeout)
                except psutil.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=timeout)
            self._remove_live_markers()
            self._write_state({"state": "stopped"})
            return LocalStatus(state="stopped")

    def _complete_model_path(self, model_id: str) -> Path:
        model = self._model(model_id)
        for release in model.get("releases") or []:
            if not isinstance(release, dict):
                continue
            target = self.root / "models" / model_id / str(release.get("quant") or "")
            marker = target / "complete.json"
            if not marker.is_file():
                continue
            urls = release.get("urls") or []
            checksums = release.get("sha256") or []
            files: list[Path] = []
            valid = len(urls) == len(checksums) and bool(urls)
            for url, expected in zip(urls, checksums, strict=True):
                path = target / Path(urllib.parse.urlparse(str(url)).path).name
                if not path.is_file() or self._file_digest(path, str(expected)) != str(expected).lower():
                    valid = False
                    break
                files.append(path)
            license_path = target / "LICENSE"
            license_expected = str(model.get("license_file_sha256") or "")
            if (
                not license_path.is_file()
                or not license_expected
                or self._file_digest(license_path, license_expected) != license_expected.lower()
            ):
                valid = False
            if valid:
                return files[0]
        raise LocalRuntimeError(f"model {model_id!r} is not completely downloaded; run `hermes local pull`")

    @contextmanager
    def _operation_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".operation.lock"
        for attempt in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError as exc:
                try:
                    owner = int(path.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    owner = -1
                if attempt == 0 and not psutil.pid_exists(owner):
                    path.unlink(missing_ok=True)
                    continue
                raise LocalRuntimeError("another local runtime operation is in progress") from exc
        else:  # pragma: no cover - loop either breaks or raises
            raise LocalRuntimeError("could not acquire local runtime operation lock")
        try:
            with os.fdopen(fd, "w", encoding="ascii") as lock_file:
                lock_file.write(str(os.getpid()))
            yield
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _assert_port_available(port: int) -> None:
        if not 1 <= port <= 65535:
            raise LocalRuntimeError("port must be between 1 and 65535")
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise LocalRuntimeError(f"port {port} is already in use") from exc

    @staticmethod
    def _health_ready(port: int) -> bool:
        try:
            for path in ("/health", "/v1/models"):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=0.5) as response:
                    if response.status != 200:
                        return False
                    json.loads(response.read().decode("utf-8"))
            return True
        except (OSError, ValueError, urllib.error.URLError):
            return False

    @staticmethod
    def _is_managed_process(state: dict[str, Any]) -> bool:
        pid = state.get("pid")
        expected_server = str(state.get("server") or "")
        expected_created = state.get("process_create_time")
        if not isinstance(pid, int) or not expected_server:
            return False
        try:
            process = psutil.Process(pid)
            if expected_created is not None and abs(process.create_time() - float(expected_created)) > 0.01:
                return False
            expected = str(Path(expected_server).resolve())
            if str(Path(process.exe()).resolve()) == expected:
                return True
            argv = [str(Path(arg).resolve()) for arg in process.cmdline()[:2] if arg]
            return expected in argv
        except (psutil.Error, OSError, ValueError):
            return False

    def _status_from_state(self, state: dict[str, Any]) -> LocalStatus:
        port = state.get("port") if isinstance(state.get("port"), int) else None
        return LocalStatus(
            state=str(state.get("state") or "error"),
            pid=state.get("pid") if isinstance(state.get("pid"), int) else None,
            port=port,
            model_id=state.get("model_id"),
            backend=state.get("backend"),
            base_url=f"http://127.0.0.1:{port}/v1" if port is not None else None,
            stdout_log=Path(state["stdout_log"]) if state.get("stdout_log") else None,
            stderr_log=Path(state["stderr_log"]) if state.get("stderr_log") else None,
            error=state.get("error"),
        )

    def _write_state(self, state: dict[str, Any]) -> None:
        self._atomic_json(self.root / "state.json", state)

    def _remove_live_markers(self) -> None:
        for name in ("llama-server.pid", "port", "llama-server.lock"):
            (self.root / name).unlink(missing_ok=True)

    @staticmethod
    def _find_server(root: Path, *, backend: str) -> Path | None:
        if not is_runtime_cache_complete(root, backend=backend, target_platform=sys.platform):
            return None
        return _server_candidate(root, sys.platform)

    @staticmethod
    def _extract_archive(archive: Path, destination: Path) -> None:
        destination_root = destination.resolve()

        def safe_target(name: str) -> Path:
            target = (destination / name).resolve()
            try:
                target.relative_to(destination_root)
            except ValueError as exc:
                raise LocalRuntimeError(f"unsafe archive member: {name}") from exc
            return target

        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    safe_target(member.filename)
                    bundle.extract(member, destination)
                    mode = member.external_attr >> 16
                    extracted = destination / member.filename
                    if mode and extracted.is_file():
                        extracted.chmod(mode)
            return
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as bundle:
                members = bundle.getmembers()
                for member in members:
                    safe_target(member.name)
                    if not (member.isdir() or member.isreg() or member.issym() or member.islnk()):
                        raise LocalRuntimeError(f"unsupported tar member type: {member.name}")
                    if member.issym():
                        safe_target(str(Path(member.name).parent / member.linkname))
                    elif member.islnk():
                        safe_target(member.linkname)
                bundle.extractall(destination, members=members)
            return
        raise LocalRuntimeError(f"unsupported runtime archive: {archive.name}")

    def _model(self, model_id: str) -> dict[str, Any]:
        for model in self.manifest.get("models") or []:
            if isinstance(model, dict) and model.get("id") == model_id:
                return model
        raise LocalRuntimeError(f"unknown local model: {model_id}")

    @staticmethod
    def _file_digest(path: Path, expected: str) -> str:
        normalized = expected.lower()
        if len(normalized) == 64:
            digest = hashlib.sha256()
        elif len(normalized) == 40:
            digest = hashlib.sha1()  # noqa: S324 - verified Hugging Face Git blob id
            digest.update(f"blob {path.stat().st_size}\0".encode("ascii"))
        else:
            raise LocalRuntimeError("manifest checksum must be a SHA-256 or Git blob digest")
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _download_verified(self, url: str, destination: Path, expected: str) -> None:
        if destination.is_file():
            if self._file_digest(destination, expected) == expected.lower():
                return

        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temp = Path(temp_name)
            actual = self._file_digest(temp, expected)
            if actual != expected.lower():
                raise LocalRuntimeError(
                    f"checksum mismatch for {destination.name}: expected {expected}, got {actual}"
                )
            temp.replace(destination)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(payload, output, indent=2, sort_keys=True)
                output.write("\n")
            Path(temp_name).replace(path)
        finally:
            Path(temp_name).unlink(missing_ok=True)
