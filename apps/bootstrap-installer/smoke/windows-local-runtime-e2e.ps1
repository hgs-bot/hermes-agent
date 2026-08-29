[CmdletBinding()]
param(
    [string]$InstallerPath = "",
    [string]$ExpectedInstallerSha256 = "",
    [string]$InstallHome = (Join-Path $env:LOCALAPPDATA "hermes"),
    [string]$SmokeHome = (Join-Path $env:LOCALAPPDATA "hermes-local-runtime-e2e-8fe1c354"),
    [int]$Port = 18080,
    [switch]$SkipInstaller,
    [switch]$KeepRuntime,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Get-CacheSnapshot {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return @()
    }
    return @(
        Get-ChildItem -LiteralPath $Root -File -Recurse |
            Where-Object { $_.Extension -notin @(".json", ".lock") } |
            Sort-Object FullName |
            ForEach-Object {
                [pscustomobject]@{
                    path = $_.FullName.Substring($Root.Length).TrimStart('\')
                    size = $_.Length
                    write_ticks = $_.LastWriteTimeUtc.Ticks
                }
            }
    )
}

function Compare-CacheSnapshots {
    param([object[]]$Before, [object[]]$After)
    $BeforeJson = ConvertTo-Json -InputObject @($Before) -Depth 4 -Compress
    $AfterJson = ConvertTo-Json -InputObject @($After) -Depth 4 -Compress
    return $BeforeJson -eq $AfterJson
}

function Resolve-Backend {
    param([int]$VramDeltaMiB)
    if ($VramDeltaMiB -ge 1024) {
        return "cuda"
    }
    return "cpu-or-unknown"
}

function Get-GpuMemoryUsedMiB {
    $NvidiaSmi = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($null -eq $NvidiaSmi) {
        throw "nvidia-smi.exe is required for the CUDA/VRAM smoke gate."
    }
    $Raw = (& $NvidiaSmi.Source --query-gpu=memory.used --format=csv,noheader,nounits 2>&1 | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or $null -eq $Raw) {
        throw "nvidia-smi failed: $Raw"
    }
    $Value = 0
    if (-not [int]::TryParse(([string]$Raw).Trim(), [ref]$Value)) {
        throw "Could not parse nvidia-smi memory.used output: $Raw"
    }
    return $Value
}

function Invoke-Hermes {
    param(
        [string]$HermesCli,
        [string[]]$Arguments
    )
    $Output = @(& $HermesCli @Arguments 2>&1)
    $ExitCode = $LASTEXITCODE
    foreach ($Line in $Output) {
        Write-Host $Line
    }
    if ($ExitCode -ne 0) {
        throw "hermes $($Arguments -join ' ') failed with exit code $ExitCode"
    }
    return ($Output -join "`n")
}

function Wait-ForHermesInstall {
    param([string]$HermesCli, [string]$CompleteMarker, [int]$TimeoutSeconds = 1800)
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ((Test-Path -LiteralPath $HermesCli -PathType Leaf) -and (Test-Path -LiteralPath $CompleteMarker -PathType Leaf)) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Hermes bootstrap did not complete within $TimeoutSeconds seconds (CLI: $HermesCli; marker: $CompleteMarker)."
}

function Start-HermesBootstrapIfNeeded {
    param([string]$HermesCli, [string]$CompleteMarker)
    if ((Test-Path -LiteralPath $HermesCli -PathType Leaf) -and (Test-Path -LiteralPath $CompleteMarker -PathType Leaf)) {
        return
    }
    if ($null -ne (Get-Process -Name "Hermes" -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        return
    }
    $Candidates = @(
        (Join-Path $env:LOCALAPPDATA "Hermes\Hermes.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Hermes\Hermes.exe"),
        (Join-Path $env:ProgramFiles "Hermes\Hermes.exe")
    )
    $BootstrapExe = $Candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ($null -eq $BootstrapExe) {
        throw "Installed Hermes bootstrap executable was not found in the expected NSIS locations."
    }
    Write-Host "Starting Hermes bootstrap UI. Complete the visible setup window once."
    Start-Process -FilePath $BootstrapExe | Out-Null
}

function Invoke-ApiProbe {
    param([string]$Python, [int]$ApiPort, [string]$OutputPath)
    $Probe = @'
import json
import sys
import time
import urllib.request

port = int(sys.argv[1])
out_path = sys.argv[2]
base = f"http://127.0.0.1:{port}/v1/chat/completions"
model = "qwen2.5-7b-instruct"

def post(payload):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base, data=body, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(request, timeout=180)

stream_payload = {
    "model": model,
    "messages": [{"role": "user", "content": "Antworte ausschliesslich mit: 1, 2, 3, 4, 5"}],
    "temperature": 0,
    "stream": True,
}
started = time.perf_counter()
first_token_ms = None
chunks = []
with post(stream_payload) as response:
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        event = json.loads(data)
        content = event["choices"][0].get("delta", {}).get("content") or ""
        if content:
            if first_token_ms is None:
                first_token_ms = round((time.perf_counter() - started) * 1000)
            chunks.append(content)
elapsed = time.perf_counter() - started
text = "".join(chunks).strip()

tool_payload = {
    "model": model,
    "messages": [{"role": "user", "content": "Wie ist das Wetter in Berlin? Nutze das Werkzeug."}],
    "temperature": 0,
    "stream": False,
    "tools": [{
        "type": "function",
        "function": {
            "name": "wetter_abfragen",
            "description": "Fragt das Wetter fuer eine Stadt ab",
            "parameters": {
                "type": "object",
                "properties": {"stadt": {"type": "string"}},
                "required": ["stadt"],
            },
        },
    }],
    "tool_choice": "auto",
}
with post(tool_payload) as response:
    tool_response = json.loads(response.read().decode("utf-8"))
choice = tool_response["choices"][0]
tool_calls = choice.get("message", {}).get("tool_calls") or []
call = tool_calls[0] if tool_calls else None
result = {
    "stream_text": text,
    "first_token_ms": first_token_ms,
    "stream_elapsed_s": round(elapsed, 3),
    "stream_chunks_per_second": round(len(chunks) / elapsed, 2) if elapsed else None,
    "tool_call_ok": bool(call),
    "tool_name": call.get("function", {}).get("name") if call else None,
    "tool_arguments": call.get("function", {}).get("arguments") if call else None,
    "tool_finish_reason": choice.get("finish_reason"),
}
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2)
print(json.dumps(result, ensure_ascii=False))
'@
    $Probe | & $Python - $ApiPort $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "OpenAI-compatible streaming/tool probe failed with exit code $LASTEXITCODE"
    }
    return Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json
}

function Invoke-SelfTest {
    $Root = Join-Path ([System.IO.Path]::GetTempPath()) ("hermes-local-e2e-selftest-" + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $Root "a.gguf") -Value "fixture" -Encoding Ascii
        $First = Get-CacheSnapshot -Root $Root
        $Second = Get-CacheSnapshot -Root $Root
        Assert-True (Compare-CacheSnapshots -Before $First -After $Second) "Identical cache snapshots must compare equal."
        Add-Content -LiteralPath (Join-Path $Root "a.gguf") -Value "changed" -Encoding Ascii
        $Third = Get-CacheSnapshot -Root $Root
        Assert-True (-not (Compare-CacheSnapshots -Before $First -After $Third)) "Changed cache snapshots must compare unequal."
        Assert-True ((Resolve-Backend -VramDeltaMiB 4908) -eq "cuda") "V6 VRAM delta must resolve to CUDA."
        Assert-True ((Resolve-Backend -VramDeltaMiB 0) -eq "cpu-or-unknown") "Zero VRAM delta must not resolve to CUDA."
        [pscustomobject]@{ ok = $true; cache_idempotency = $true; cuda_delta_inference = $true } | ConvertTo-Json -Compress
    }
    finally {
        Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    Invoke-SelfTest
    exit 0
}

if ($env:OS -ne "Windows_NT") {
    throw "This E2E smoke gate must run on native Windows."
}

if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $InstallerPath = Join-Path $PSScriptRoot "Hermes_0.0.1_x64-setup.exe"
}
$InstallerPath = [System.IO.Path]::GetFullPath($InstallerPath)
$ExpectedInstallerSha256 = $ExpectedInstallerSha256.ToLowerInvariant()
$HermesCli = Join-Path $InstallHome "hermes-agent\venv\Scripts\hermes.exe"
$Python = Join-Path $InstallHome "hermes-agent\venv\Scripts\python.exe"
$CompleteMarker = Join-Path $InstallHome "hermes-agent\.hermes-bootstrap-complete"
$ContractSmoke = Join-Path $PSScriptRoot "windows-local-runtime-smoke.ps1"
$ResultPath = Join-Path $PSScriptRoot "results-e2e-8fe1c354.json"
$PreviousHermesHome = $env:HERMES_HOME
$StartedRuntime = $false
$Result = [ordered]@{
    schema = "hermes-local-runtime-e2e@1"
    ok = $false
    installer_version = "0.0.1"
    build_commit = "8fe1c354310c6b3dea20a345873acc06597c86ba"
    installer_sha256 = $null
    install_contract = $false
    cache_idempotent = $false
    lifecycle = $false
    backend = $null
    backend_basis = $null
    vram_before_mib = $null
    vram_ready_mib = $null
    vram_delta_mib = $null
    vram_after_stop_mib = $null
    cold_start_to_health_s = $null
    first_token_ms = $null
    stream_text = $null
    stream_chunks_per_second = $null
    tool_call = $false
    tool_name = $null
    tool_arguments = $null
    stdout_log = $null
    stderr_log = $null
    error = $null
}

try {
    Assert-True (Test-Path -LiteralPath $InstallerPath -PathType Leaf) "Installer is missing: $InstallerPath"
    Assert-True (-not [string]::IsNullOrWhiteSpace($ExpectedInstallerSha256)) "ExpectedInstallerSha256 must be supplied by the versioned smoke command."
    $ActualHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $Result.installer_sha256 = $ActualHash
    Assert-True ($ActualHash -eq $ExpectedInstallerSha256) "Installer SHA256 mismatch: expected $ExpectedInstallerSha256, got $ActualHash"

    if (-not $SkipInstaller) {
        Write-Host "Launching verified Hermes installer. Complete the visible setup window; this runner will continue automatically."
        $Installer = Start-Process -FilePath $InstallerPath -PassThru -Wait
        Assert-True ($Installer.ExitCode -eq 0) "NSIS installer exited with code $($Installer.ExitCode)."
    }
    Start-HermesBootstrapIfNeeded -HermesCli $HermesCli -CompleteMarker $CompleteMarker
    Wait-ForHermesInstall -HermesCli $HermesCli -CompleteMarker $CompleteMarker
    Assert-True (Test-Path -LiteralPath $Python -PathType Leaf) "Installed Python is missing: $Python"
    Assert-True (Test-Path -LiteralPath $ContractSmoke -PathType Leaf) "Contract smoke script is missing: $ContractSmoke"

    $ContractOutput = (& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ContractSmoke -InstallHome $InstallHome 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Install/manifest/cache contract smoke failed:`n$ContractOutput"
    }
    $Result.install_contract = $true

    New-Item -ItemType Directory -Path $SmokeHome -Force | Out-Null
    $env:HERMES_HOME = $SmokeHome
    Invoke-Hermes -HermesCli $HermesCli -Arguments @("local", "stop") | Out-Null

    $PullWatch = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Hermes -HermesCli $HermesCli -Arguments @("local", "pull", "--backend", "cuda") | Out-Null
    $PullWatch.Stop()
    $CacheRoot = Join-Path $SmokeHome "local-runtime"
    $CacheBefore = Get-CacheSnapshot -Root $CacheRoot
    Assert-True ($CacheBefore.Count -gt 0) "First pull produced no local-runtime cache files."

    $SecondPullWatch = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Hermes -HermesCli $HermesCli -Arguments @("local", "pull", "--backend", "cuda") | Out-Null
    $SecondPullWatch.Stop()
    $CacheAfter = Get-CacheSnapshot -Root $CacheRoot
    $Result.cache_idempotent = Compare-CacheSnapshots -Before $CacheBefore -After $CacheAfter
    Assert-True $Result.cache_idempotent "Second pull changed cache files; cache is not idempotent."

    $Result.vram_before_mib = Get-GpuMemoryUsedMiB
    $StartWatch = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Hermes -HermesCli $HermesCli -Arguments @("local", "start", "--backend", "cuda", "--port", ([string]$Port), "--timeout", "180") | Out-Null
    $StartWatch.Stop()
    $StartedRuntime = $true
    $Result.cold_start_to_health_s = [Math]::Round($StartWatch.Elapsed.TotalSeconds, 3)

    $StatusRaw = Invoke-Hermes -HermesCli $HermesCli -Arguments @("local", "status", "--json")
    $Status = $StatusRaw | ConvertFrom-Json
    Assert-True ($Status.state -eq "ready") "Runtime status is not ready: $StatusRaw"
    Assert-True ($Status.backend -eq "cuda") "Runtime status backend is not cuda: $StatusRaw"
    Assert-True ($Status.stdout_log -ne $Status.stderr_log) "Runtime stdout/stderr logs are not separated."
    $Result.stdout_log = $Status.stdout_log
    $Result.stderr_log = $Status.stderr_log
    $Result.vram_ready_mib = Get-GpuMemoryUsedMiB
    $Result.vram_delta_mib = $Result.vram_ready_mib - $Result.vram_before_mib
    $Result.backend = Resolve-Backend -VramDeltaMiB $Result.vram_delta_mib
    $Result.backend_basis = "nvidia-smi memory.used delta while hermes-managed llama-server is ready"
    Assert-True ($Result.backend -eq "cuda") "CUDA was not proven by VRAM delta: $($Result.vram_delta_mib) MiB"

    $ApiResultPath = Join-Path $SmokeHome "api-probe.json"
    $Api = Invoke-ApiProbe -Python $Python -ApiPort $Port -OutputPath $ApiResultPath
    $Result.first_token_ms = $Api.first_token_ms
    $Result.stream_text = $Api.stream_text
    $Result.stream_chunks_per_second = $Api.stream_chunks_per_second
    $Result.tool_call = [bool]$Api.tool_call_ok
    $Result.tool_name = $Api.tool_name
    $Result.tool_arguments = $Api.tool_arguments
    Assert-True ($Result.stream_text -match "1.*2.*3.*4.*5") "Streaming response did not contain 1 through 5: $($Result.stream_text)"
    Assert-True $Result.tool_call "Model did not emit a tool call."
    Assert-True ($Result.tool_name -eq "wetter_abfragen") "Unexpected tool name: $($Result.tool_name)"

    Invoke-Hermes -HermesCli $HermesCli -Arguments @("local", "stop", "--timeout", "20") | Out-Null
    $StartedRuntime = $false
    Start-Sleep -Seconds 2
    $StoppedStatusRaw = Invoke-Hermes -HermesCli $HermesCli -Arguments @("local", "status", "--json")
    $StoppedStatus = $StoppedStatusRaw | ConvertFrom-Json
    Assert-True ($StoppedStatus.state -eq "stopped") "Runtime did not stop cleanly: $StoppedStatusRaw"
    $Result.vram_after_stop_mib = Get-GpuMemoryUsedMiB
    Assert-True ($Result.vram_after_stop_mib -le ($Result.vram_before_mib + 512)) "VRAM did not return near baseline after stop."
    $Result.lifecycle = $true
    $Result.ok = $true
}
catch {
    $Result.error = $_.Exception.Message
    throw
}
finally {
    if ($StartedRuntime) {
        try {
            & $HermesCli local stop --timeout 20 | Out-Null
        }
        catch {
            Write-Warning "Emergency runtime stop failed: $($_.Exception.Message)"
        }
    }
    $env:HERMES_HOME = $PreviousHermesHome
    $Result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    Write-Host "E2E result: $ResultPath"
    Write-Host ($Result | ConvertTo-Json -Depth 8)
    if (-not $KeepRuntime -and $Result.ok) {
        Write-Host "Validated runtime cache retained at $SmokeHome for repeat/idempotency inspection."
    }
}
