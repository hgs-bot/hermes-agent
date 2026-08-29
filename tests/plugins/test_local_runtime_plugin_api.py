from __future__ import annotations

import importlib.util
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


API_PATH = Path(__file__).parents[2] / "plugins" / "local-runtime" / "dashboard" / "plugin_api.py"


@pytest.fixture
def api_module():
    spec = importlib.util.spec_from_file_location("local_runtime_plugin_api", API_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(api_module, monkeypatch):
    @dataclass
    class Status:
        state: str = "stopped"
        pid: int | None = None
        port: int | None = None
        model_id: str | None = None
        backend: str | None = None
        base_url: str | None = None
        stdout_log: Path | None = None
        stderr_log: Path | None = None
        error: str | None = None

    class Manager:
        manifest = {
            "schema": "hermes-local-manifest@1",
            "runtimes": [
                {
                    "backend": "vulkan",
                    "machine": platform.machine().lower(),
                    "platform": sys.platform,
                }
            ],
            "models": [
                {
                    "id": "qwen-local",
                    "display_name": "Qwen Local",
                    "license": "Apache-2.0",
                    "tool_calling": True,
                    "recommended": True,
                    "releases": [
                        {
                            "quant": "Q4_K_M",
                            "context_length": 16384,
                            "size_bytes": 4_000_000_000,
                            "vram_estimate_gb": 4.8,
                            "backend": "cuda",
                            "tokens_per_second": 57.4,
                        }
                    ],
                }
            ],
        }

        def __init__(self):
            self.status_value = Status(stdout_log=Path("/tmp/out.log"))
            self.calls = []

        def status(self):
            self.calls.append(("status",))
            return self.status_value

        def pull_model(self, model_id, quant=None):
            self.calls.append(("pull_model", model_id, quant))
            return type("Pull", (), {"state": "ready", "files": (Path("one.gguf"), Path("two.gguf"))})()

        def pull_runtime(self, backend):
            self.calls.append(("pull_runtime", backend))
            return type("RuntimePull", (), {"state": "ready", "server": Path("llama-server")})()

        def _model(self, model_id):
            return next(model for model in self.manifest["models"] if model["id"] == model_id)

        def start(self, model_id, *, backend, port):
            self.calls.append(("start", model_id, backend, port))
            self.status_value = Status(
                state="ready",
                pid=42,
                port=port,
                model_id=model_id,
                backend=backend,
                base_url=f"http://127.0.0.1:{port}/v1",
            )
            return self.status_value

        def stop(self):
            self.calls.append(("stop",))
            self.status_value = Status()
            return self.status_value

    manager = Manager()
    monkeypatch.setattr(api_module, "_manager", lambda: manager)
    app = FastAPI()
    app.include_router(api_module.router)
    return TestClient(app), manager


def test_catalog_and_status_are_backend_owned_and_json_safe(client):
    http, manager = client

    catalog = http.get("/catalog")
    status = http.get("/status")

    assert catalog.status_code == 200
    assert catalog.json() == {
        "schema": "hermes-local-manifest@1",
        "models": [
            {
                "id": "qwen-local",
                "display_name": "Qwen Local",
                "license": "Apache-2.0",
                "tool_calling": True,
                "recommended": True,
                "releases": [
                    {
                        "quant": "Q4_K_M",
                        "context_length": 16384,
                        "size_bytes": 4_000_000_000,
                        "vram_estimate_gb": 4.8,
                        "backend": "cuda",
                        "runtime_backend": "vulkan",
                        "tokens_per_second": 57.4,
                    }
                ],
            }
        ],
    }
    assert status.status_code == 200
    assert status.json()["state"] == "stopped"
    assert status.json()["stdout_log"] == "/tmp/out.log"
    assert manager.calls == [("status",)]


def test_pull_start_and_stop_delegate_to_runtime_manager(client, api_module, monkeypatch):
    http, manager = client
    configured = []
    monkeypatch.setattr(
        api_module,
        "configure_local_provider",
        lambda **values: configured.append(values),
    )

    pulled = http.post(
        "/pull",
        json={"model_id": "qwen-local", "quant": "Q4_K_M", "backend": "cuda"},
    )
    started = http.post("/start", json={"model_id": "qwen-local", "backend": "cuda", "port": 11435})
    stopped = http.post("/stop")

    assert pulled.status_code == 200
    assert pulled.json() == {
        "state": "ready",
        "files": ["one.gguf", "two.gguf"],
        "runtime": "llama-server",
    }
    assert started.status_code == 200
    assert started.json() == {
        "state": "ready",
        "pid": 42,
        "port": 11435,
        "model_id": "qwen-local",
        "backend": "cuda",
        "base_url": "http://127.0.0.1:11435/v1",
        "stdout_log": None,
        "stderr_log": None,
        "error": None,
    }
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopped"
    assert manager.calls == [
        ("pull_model", "qwen-local", "Q4_K_M"),
        ("pull_runtime", "cuda"),
        ("start", "qwen-local", "cuda", 11435),
        ("stop",),
    ]
    assert configured == [
        {
            "model_id": "qwen-local",
            "base_url": "http://127.0.0.1:11435/v1",
            "context_length": 16384,
        }
    ]


def test_runtime_failures_surface_as_actionable_http_errors(client, api_module, monkeypatch):
    http, manager = client

    def fail(*_args, **_kwargs):
        raise api_module.LocalRuntimeError("checksum mismatch")

    monkeypatch.setattr(manager, "pull_model", fail)

    response = http.post("/pull", json={"model_id": "qwen-local"})

    assert response.status_code == 409
    assert response.json() == {"detail": "checksum mismatch"}
