"""Command handler for ``hermes local``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hermes_cli.local_runtime import (
    LocalRuntimeError,
    LocalRuntimeManager,
    LocalStatus,
    configure_local_provider,
    load_builtin_manifest,
)


def _default_backend() -> str:
    return "cpu" if sys.platform == "darwin" else "vulkan"


def _status_payload(status: LocalStatus) -> dict[str, object]:
    payload: dict[str, object] = {"state": status.state}
    for key in ("pid", "port", "model_id", "backend", "base_url", "error"):
        value = getattr(status, key)
        if value is not None:
            payload[key] = value
    if status.stdout_log is not None:
        payload["stdout_log"] = str(status.stdout_log)
    if status.stderr_log is not None:
        payload["stderr_log"] = str(status.stderr_log)
    return payload


def _context_length(manager: LocalRuntimeManager, model_id: str) -> int:
    model = manager._model(model_id)
    releases = model.get("releases") or []
    if releases and isinstance(releases[0], dict):
        value = releases[0].get("context_length")
        if isinstance(value, int) and value > 0:
            return value
    return 8192


def local_command(args) -> int:
    manager = LocalRuntimeManager(manifest=load_builtin_manifest())
    action = args.local_action
    backend = getattr(args, "backend", None) or _default_backend()
    try:
        if action == "pull":
            if not args.runtime_only:
                result = manager.pull_model(args.model, quant=args.quant)
                print(f"model ready: {args.model} ({len(result.files)} file(s))")
            if not args.model_only:
                runtime = manager.pull_runtime(backend)
                print(f"runtime ready: {runtime.server}")
            return 0
        if action == "start":
            status = manager.start(
                args.model,
                backend=backend,
                port=args.port,
                timeout=args.timeout,
            )
            configure_local_provider(
                model_id=args.model,
                base_url=status.base_url or f"http://127.0.0.1:{args.port}/v1",
                context_length=_context_length(manager, args.model),
            )
            print(json.dumps(_status_payload(status), indent=2))
            return 0
        if action == "stop":
            status = manager.stop(timeout=args.timeout)
            print(status.state)
            return 0
        if action == "status":
            status = manager.status()
            payload = _status_payload(status)
            if args.json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(f"state: {status.state}")
                for key, value in payload.items():
                    if key != "state":
                        print(f"{key}: {value}")
            return 0 if status.state != "error" else 1
    except (LocalRuntimeError, OSError) as exc:
        print(f"local runtime error: {exc}", file=sys.stderr)
        return 1
    print(f"unknown local action: {action}", file=sys.stderr)
    return 2
