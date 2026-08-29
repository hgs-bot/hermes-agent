"""Backend-owned API for the bundled Hermes local-runtime Desktop plugin."""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hermes_cli.local_runtime import (
    LocalRuntimeError,
    LocalRuntimeManager,
    configure_local_provider,
    load_builtin_manifest,
)

router = APIRouter()


class PullRequest(BaseModel):
    model_id: str = Field(min_length=1)
    quant: str | None = None
    backend: str = Field(default="vulkan", pattern="^(cpu|vulkan|cuda)$")


class StartRequest(BaseModel):
    model_id: str = Field(min_length=1)
    backend: str = Field(default="vulkan", pattern="^(cpu|vulkan|cuda)$")
    port: int = Field(default=11435, ge=1, le=65535)


def _manager() -> LocalRuntimeManager:
    return LocalRuntimeManager(manifest=load_builtin_manifest())


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _status_payload(status: Any) -> dict[str, Any]:
    return _json_safe(asdict(status))


def _catalog_payload(manager: LocalRuntimeManager) -> dict[str, Any]:
    model_fields = (
        "id",
        "display_name",
        "license",
        "tool_calling",
        "recommended",
    )
    release_fields = (
        "quant",
        "context_length",
        "size_bytes",
        "vram_estimate_gb",
        "backend",
        "tokens_per_second",
    )
    runtime_backends = [
        str(runtime["backend"])
        for runtime in manager.manifest.get("runtimes") or []
        if isinstance(runtime, dict)
        and runtime.get("platform") == sys.platform
        and str(runtime.get("machine", "")).lower() == platform.machine().lower()
        and runtime.get("backend")
    ]
    models = []
    for model in manager.manifest.get("models") or []:
        if not isinstance(model, dict):
            continue
        item = {field: model[field] for field in model_fields if field in model}
        item["releases"] = []
        for release in model.get("releases") or []:
            if not isinstance(release, dict):
                continue
            release_item = {field: release[field] for field in release_fields if field in release}
            measured_backend = str(release.get("backend") or "")
            release_item["runtime_backend"] = (
                measured_backend if measured_backend in runtime_backends else next(iter(runtime_backends), measured_backend)
            )
            item["releases"].append(release_item)
        models.append(item)
    return {"schema": manager.manifest.get("schema"), "models": models}


def _context_length(manager: LocalRuntimeManager, model_id: str) -> int:
    for model in manager.manifest.get("models") or []:
        if not isinstance(model, dict) or model.get("id") != model_id:
            continue
        for release in model.get("releases") or []:
            if isinstance(release, dict) and isinstance(release.get("context_length"), int):
                return release["context_length"]
    return 8192


def _operation(run):
    try:
        return run()
    except LocalRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    return _catalog_payload(_manager())


@router.get("/status")
def status() -> dict[str, Any]:
    return _status_payload(_manager().status())


@router.post("/pull")
def pull(body: PullRequest) -> dict[str, Any]:
    manager = _manager()
    result = _operation(lambda: manager.pull_model(body.model_id, body.quant))
    runtime = _operation(lambda: manager.pull_runtime(body.backend))
    return {
        "state": result.state,
        "files": [str(path) for path in result.files],
        "runtime": str(runtime.server),
    }


@router.post("/start")
def start(body: StartRequest) -> dict[str, Any]:
    manager = _manager()
    status = _operation(
        lambda: manager.start(
            body.model_id,
            backend=body.backend,
            port=body.port,
        )
    )
    configure_local_provider(
        model_id=body.model_id,
        base_url=status.base_url or f"http://127.0.0.1:{body.port}/v1",
        context_length=_context_length(manager, body.model_id),
    )
    return _status_payload(status)


@router.post("/stop")
def stop() -> dict[str, Any]:
    return _status_payload(_operation(lambda: _manager().stop()))
