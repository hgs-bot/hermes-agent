from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from hermes_cli.subcommands.local import build_local_parser


def _sentinel(_args):
    return 0


def test_local_parser_exposes_pull_start_stop_status():
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_local_parser(subparsers, cmd_local=_sentinel)

    pull = parser.parse_args(["local", "pull", "--backend", "cuda"])
    start = parser.parse_args(["local", "start", "--port", "8123"])
    stop = parser.parse_args(["local", "stop"])
    status = parser.parse_args(["local", "status", "--json"])

    assert pull.local_action == "pull"
    assert pull.backend == "cuda"
    assert start.local_action == "start"
    assert start.port == 8123
    assert stop.local_action == "stop"
    assert status.local_action == "status"
    assert status.json is True
    assert all(ns.func is _sentinel for ns in (pull, start, stop, status))


def test_real_cli_status_uses_isolated_hermes_home(tmp_path):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "local", "status", "--json"],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"state": "stopped"}
