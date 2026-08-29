[CmdletBinding()]
param(
    [string]$InstallHome = (Join-Path $env:LOCALAPPDATA "hermes"),
    [switch]$KeepSmokeHome
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

if ($env:OS -ne "Windows_NT") {
    throw "This smoke gate must run on native Windows."
}

$InstallRoot = Join-Path $InstallHome "hermes-agent"
$HermesCli = Join-Path $InstallRoot "venv\Scripts\hermes.exe"
$Python = Join-Path $InstallRoot "venv\Scripts\python.exe"
foreach ($Required in @($HermesCli, $Python)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Installed Hermes executable is missing: $Required"
    }
}

$SmokeHome = Join-Path ([System.IO.Path]::GetTempPath()) ("hermes-local-smoke-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $SmokeHome -Force | Out-Null
$PreviousHermesHome = $env:HERMES_HOME
$env:HERMES_HOME = $SmokeHome

$ProbeSource = @'
import importlib
import json
import os
import string
import sys
from pathlib import Path

from hermes_cli.local_runtime import (
    LocalRuntimeManager,
    is_runtime_cache_complete,
    load_builtin_manifest,
)

if sys.platform != "win32":
    raise AssertionError(f"native Windows required, got {sys.platform}")

# The installed console script already imported main/subcommands. Explicitly
# import every local-runtime module so missing wheel/editable-install files fail.
modules = [
    importlib.import_module("hermes_cli.local_runtime"),
    importlib.import_module("hermes_cli.local_runtime_cmd"),
    importlib.import_module("hermes_cli.local_runtime_manifest"),
    importlib.import_module("hermes_cli.subcommands.local"),
]
module_files = [Path(module.__file__).resolve() for module in modules]
if not all(path.is_file() for path in module_files):
    raise AssertionError(f"installed local-runtime module missing: {module_files}")
if any(path.stat().st_size >= 1024 * 1024 for path in module_files):
    raise AssertionError("local-runtime Python modules unexpectedly contain bundled binary payloads")
package_root = module_files[0].parent
for suffix in (".gguf", ".zip", ".tar", ".gz", ".dll"):
    payloads = list(package_root.rglob(f"*{suffix}"))
    if payloads:
        raise AssertionError(f"local-runtime assets must stay lazy, found {payloads}")

manifest = load_builtin_manifest()
cuda = next(
    item
    for item in manifest["runtimes"]
    if item["platform"] == "win32"
    and item["machine"] == "amd64"
    and item["backend"] == "cuda"
)
assets = cuda["assets"]
if {asset["kind"] for asset in assets} != {"build", "cuda-runtime"}:
    raise AssertionError(f"Windows CUDA build/cudart split missing: {assets}")
hex_chars = set(string.hexdigits)
for asset in assets:
    sha256 = asset["sha256"]
    if len(sha256) != 64 or any(char not in hex_chars for char in sha256):
        raise AssertionError(f"asset is not SHA-256 pinned: {asset}")
    if not asset["url"].startswith("https://github.com/ggml-org/llama.cpp/releases/download/"):
        raise AssertionError(f"unexpected runtime origin: {asset['url']}")

home = Path(os.environ["HERMES_HOME"]).resolve()
cache = home / "local-runtime" / "smoke-cache"
cache.mkdir(parents=True)
(cache / "llama-server.exe").write_bytes(b"fixture")
(cache / "ggml-cuda.dll").write_bytes(b"fixture")
if is_runtime_cache_complete(cache, backend="cuda", target_platform="win32"):
    raise AssertionError("CUDA cache must be invalid until the separate cudart asset exists")
cudart = cache / "cudart64_12.dll"
cudart.write_bytes(b"fixture")
if not is_runtime_cache_complete(cache, backend="cuda", target_platform="win32"):
    raise AssertionError("CUDA cache must become complete with build and cudart files")
cudart.unlink()
if is_runtime_cache_complete(cache, backend="cuda", target_platform="win32"):
    raise AssertionError("deleting cudart must invalidate a previously complete cache")

manager = LocalRuntimeManager(manifest=manifest)
logs = home / "local-runtime" / "logs"
status = manager._status_from_state({
    "state": "starting",
    "stdout_log": str(logs / "llama-server.stdout.log"),
    "stderr_log": str(logs / "llama-server.stderr.log"),
})
if status.stdout_log == status.stderr_log:
    raise AssertionError("llama-server stdout and stderr must use separate paths")
if status.stdout_log.parent != logs or status.stderr_log.parent != logs:
    raise AssertionError("llama-server logs escaped the isolated local-runtime log directory")

print(json.dumps({
    "ok": True,
    "manifest_schema": manifest["schema"],
    "runtime_version": cuda["version"],
    "asset_kinds": sorted(asset["kind"] for asset in assets),
    "module_files": [str(path) for path in module_files],
    "cache_invalidation": True,
    "stdout_log": str(status.stdout_log),
    "stderr_log": str(status.stderr_log),
}, sort_keys=True))
'@

try {
    $Help = (& $HermesCli local --help 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Installed hermes.exe local --help failed:`n$Help"
    }
    foreach ($Action in @("pull", "start", "stop", "status")) {
        if ($Help -notmatch "\b$Action\b") {
            throw "Installed hermes.exe local --help is missing '$Action':`n$Help"
        }
    }

    $ProbeJson = ($ProbeSource | & $Python - 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Installed local-runtime smoke probe failed:`n$ProbeJson"
    }
    $Result = $ProbeJson | ConvertFrom-Json
    if (-not $Result.ok) {
        throw "Installed local-runtime smoke probe did not report success: $ProbeJson"
    }

    [pscustomobject]@{
        ok = $true
        install_root = $InstallRoot
        isolated_hermes_home = $SmokeHome
        local_help = $true
        runtime_version = $Result.runtime_version
        asset_kinds = $Result.asset_kinds
        cache_invalidation = $Result.cache_invalidation
        stdout_log = $Result.stdout_log
        stderr_log = $Result.stderr_log
    } | ConvertTo-Json -Depth 4
}
finally {
    $env:HERMES_HOME = $PreviousHermesHome
    if (-not $KeepSmokeHome) {
        Remove-Item -LiteralPath $SmokeHome -Recurse -Force -ErrorAction SilentlyContinue
    }
}
