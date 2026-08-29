from __future__ import annotations

import hashlib
import http.server
import json
import os
import platform
import socket
import sys
import tarfile
import textwrap
import threading
from contextlib import contextmanager
from pathlib import Path

import yaml

from hermes_cli.local_runtime import (
    LocalRuntimeManager,
    configure_local_provider,
    is_runtime_cache_complete,
    load_builtin_manifest,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _fixture_server(root: Path):
    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=str(root), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pull_model_downloads_and_verifies_all_parts_and_license(tmp_path, monkeypatch):
    home = tmp_path / "home"
    served = tmp_path / "served"
    served.mkdir()
    part_1 = b"fixture gguf part one"
    part_2 = b"fixture gguf part two"
    license_text = b"Fixture permissive license\n"
    (served / "part-1.gguf").write_bytes(part_1)
    (served / "part-2.gguf").write_bytes(part_2)
    (served / "LICENSE").write_bytes(license_text)
    monkeypatch.setenv("HERMES_HOME", str(home))

    with _fixture_server(served) as base_url:
        manifest = {
            "schema": "hermes-local-manifest@1",
            "models": [
                {
                    "id": "fixture-model",
                    "display_name": "Fixture Model",
                    "license": "Apache-2.0",
                    "license_file": f"{base_url}/LICENSE",
                    "license_file_sha256": _sha256(license_text),
                    "releases": [
                        {
                            "quant": "Q4_K_M",
                            "urls": [f"{base_url}/part-1.gguf", f"{base_url}/part-2.gguf"],
                            "sha256": [_sha256(part_1), _sha256(part_2)],
                            "size_bytes": len(part_1) + len(part_2),
                        }
                    ],
                }
            ],
        }
        result = LocalRuntimeManager(manifest=manifest).pull_model("fixture-model")

    model_dir = home / "local-runtime" / "models" / "fixture-model" / "Q4_K_M"
    assert result.state == "ready"
    assert [path.read_bytes() for path in result.files] == [part_1, part_2]
    assert (model_dir / "LICENSE").read_bytes() == license_text
    assert json.loads((model_dir / "complete.json").read_text())["model_id"] == "fixture-model"


def test_pull_runtime_extracts_the_complete_archive_and_resolves_nested_server(tmp_path, monkeypatch):
    home = tmp_path / "home"
    served = tmp_path / "served"
    payload = tmp_path / "payload" / "llama-bundle" / "bin"
    payload.mkdir(parents=True)
    server = payload / "llama-server"
    server.write_text("#!/bin/sh\nexit 0\n")
    server.chmod(0o755)
    (payload / "libggml.so").write_bytes(b"shared-library")
    (payload / "libggml-current.so").symlink_to("libggml.so")
    (payload / "runtime-data.txt").write_text("must survive extraction")
    served.mkdir()
    archive = served / "llama.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload.parent, arcname="llama-bundle")
    monkeypatch.setenv("HERMES_HOME", str(home))

    with _fixture_server(served) as base_url:
        manifest = {
            "schema": "hermes-local-manifest@1",
            "models": [],
            "runtimes": [
                {
                    "version": "fixture",
                    "platform": "linux",
                    "machine": platform.machine().lower(),
                    "backend": "vulkan",
                    "assets": [
                        {
                            "kind": "build",
                            "url": f"{base_url}/{archive.name}",
                            "sha256": _sha256(archive.read_bytes()),
                        }
                    ],
                }
            ],
        }
        result = LocalRuntimeManager(manifest=manifest).pull_runtime("vulkan")

    runtime_dir = home / "local-runtime" / "bin" / "fixture-vulkan"
    assert result.server == runtime_dir / "llama-bundle" / "bin" / "llama-server"
    assert result.server.stat().st_mode & 0o111
    assert (result.server.parent / "libggml.so").read_bytes() == b"shared-library"
    assert (result.server.parent / "libggml-current.so").read_bytes() == b"shared-library"
    assert (result.server.parent / "runtime-data.txt").read_text() == "must survive extraction"


def test_windows_cuda_cache_requires_build_dll_and_separate_cuda_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "llama-server.exe").write_bytes(b"exe")
    (runtime / "ggml-cuda.dll").write_bytes(b"backend")

    assert not is_runtime_cache_complete(runtime, backend="cuda", target_platform="win32")

    (runtime / "cudart64_12.dll").write_bytes(b"separate asset")
    assert is_runtime_cache_complete(runtime, backend="cuda", target_platform="win32")


def test_start_status_stop_uses_real_health_server_and_separate_logs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    model_bytes = b"tiny fixture model"
    license_bytes = b"fixture license"
    served = tmp_path / "served"
    served.mkdir()
    (served / "model.gguf").write_bytes(model_bytes)
    (served / "LICENSE").write_bytes(license_bytes)

    runtime_bin = home / "local-runtime" / "bin" / "fixture-vulkan" / "bundle"
    runtime_bin.mkdir(parents=True)
    server = runtime_bin / "llama-server"
    server.write_text(
        "#!" + sys.executable + "\n" + textwrap.dedent(
            """
            import argparse, json
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            parser = argparse.ArgumentParser()
            parser.add_argument('--host')
            parser.add_argument('--port', type=int)
            parser.add_argument('--model')
            parser.add_argument('--alias', required=True)
            parser.add_argument('--jinja', action='store_true')
            parser.add_argument('--n-gpu-layers')
            args = parser.parse_args()

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == '/health':
                        body = b'{"status":"ok"}'
                    elif self.path == '/v1/models':
                        body = json.dumps({'data': [{'id': args.alias}]}).encode()
                    else:
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                def log_message(self, *_args):
                    pass

            ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
            """
        )
    )
    server.chmod(0o755)
    (runtime_bin / "libggml.so").write_bytes(b"loader dependency")
    port = _free_port()

    with _fixture_server(served) as base_url:
        manifest = {
            "schema": "hermes-local-manifest@1",
            "models": [{
                "id": "fixture-model",
                "license": "Apache-2.0",
                "license_file": f"{base_url}/LICENSE",
                "license_file_sha256": _sha256(license_bytes),
                "releases": [{
                    "quant": "Q4_K_M",
                    "urls": [f"{base_url}/model.gguf"],
                    "sha256": [_sha256(model_bytes)],
                    "size_bytes": len(model_bytes),
                }],
            }],
            "runtimes": [{
                "version": "fixture",
                "platform": "linux",
                "machine": platform.machine().lower(),
                "backend": "vulkan",
                "assets": [{"kind": "build", "url": "unused", "sha256": "0" * 64}],
            }],
        }
        manager = LocalRuntimeManager(manifest=manifest)
        manager.pull_model("fixture-model")
        try:
            started = manager.start("fixture-model", backend="vulkan", port=port, timeout=5)
            current = manager.status()

            assert started.state == "ready"
            assert current.state == "ready"
            assert current.pid == started.pid
            assert current.port == port
            assert current.base_url == f"http://127.0.0.1:{port}/v1"
            assert current.stdout_log != current.stderr_log
            assert current.stdout_log.parent == home / "local-runtime" / "logs"
            if sys.platform != "win32":
                assert started.pid is not None
                assert os.getsid(started.pid) == started.pid
            assert (home / "local-runtime" / "llama-server.pid").read_text().strip() == str(started.pid)
            assert (home / "local-runtime" / "port").read_text().strip() == str(port)
            assert (home / "local-runtime" / "llama-server.lock").exists()
        finally:
            stopped = manager.stop(timeout=5)

    assert stopped.state == "stopped"
    assert manager.status().state == "stopped"
    assert not (home / "local-runtime" / "llama-server.pid").exists()


def test_builtin_manifest_encodes_verified_model_and_separate_cuda_asset():
    manifest = load_builtin_manifest()
    model = next(item for item in manifest["models"] if item["recommended"])
    assert model["license_file"]
    assert model["license_file_sha256"] == "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e"
    assert all(len(checksum) == 64 for checksum in model["releases"][0]["sha256"])
    assert model["releases"][0]["sha256"][1] == "539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a"

    cuda = next(item for item in manifest["runtimes"] if item["backend"] == "cuda")
    assert {asset["kind"] for asset in cuda["assets"]} == {"build", "cuda-runtime"}
    assert all(len(asset["sha256"]) == 64 for asset in cuda["assets"])


def test_configure_local_provider_preserves_config_and_uses_custom_transport(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(
        yaml.safe_dump({"providers": {"existing": {"api": "https://example.test/v1"}}})
    )

    configure_local_provider(
        model_id="fixture-model",
        base_url="http://127.0.0.1:8123/v1",
        context_length=16384,
    )

    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["providers"]["existing"]["api"] == "https://example.test/v1"
    assert config["providers"]["hermes-local"] == {
        "name": "Hermes Local",
        "api": "http://127.0.0.1:8123/v1",
        "transport": "chat_completions",
        "default_model": "fixture-model",
        "models": {"fixture-model": {"context_length": 16384}},
        "discover_models": False,
    }


def test_stale_operation_lock_is_recovered(tmp_path, monkeypatch):
    home = tmp_path / "home"
    runtime = home / "local-runtime"
    runtime.mkdir(parents=True)
    (runtime / ".operation.lock").write_text("999999999")
    monkeypatch.setenv("HERMES_HOME", str(home))

    manager = LocalRuntimeManager(manifest={"schema": "hermes-local-manifest@1", "models": []})

    assert manager.stop().state == "stopped"
    assert not (runtime / ".operation.lock").exists()
