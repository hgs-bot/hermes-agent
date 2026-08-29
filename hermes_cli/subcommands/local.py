"""Parser for ``hermes local`` llama-server lifecycle commands."""

from __future__ import annotations

from typing import Callable


def build_local_parser(subparsers, *, cmd_local: Callable) -> None:
    parser = subparsers.add_parser(
        "local",
        help="Manage the local llama-server runtime",
        description="Download models and manage Hermes' local llama-server sidecar",
    )
    actions = parser.add_subparsers(dest="local_action", required=True)

    pull = actions.add_parser("pull", help="Download and verify the model and llama-server")
    pull.add_argument("--model", default="qwen2.5-7b-instruct")
    pull.add_argument("--quant", default="Q4_K_M")
    pull.add_argument("--backend", choices=("vulkan", "cuda", "cpu"), default=None)
    selection = pull.add_mutually_exclusive_group()
    selection.add_argument("--model-only", action="store_true")
    selection.add_argument("--runtime-only", action="store_true")

    start = actions.add_parser("start", help="Start and health-check llama-server")
    start.add_argument("--model", default="qwen2.5-7b-instruct")
    start.add_argument("--backend", choices=("vulkan", "cuda", "cpu"), default=None)
    start.add_argument("--port", type=int, default=8080)
    start.add_argument("--timeout", type=float, default=120.0)

    stop = actions.add_parser("stop", help="Safely stop the managed llama-server")
    stop.add_argument("--timeout", type=float, default=10.0)

    status = actions.add_parser("status", help="Show local runtime lifecycle state")
    status.add_argument("--json", action="store_true")

    parser.set_defaults(func=cmd_local)
