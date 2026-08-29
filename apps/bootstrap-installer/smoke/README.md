# Windows local-runtime installer smoke gate

Run this once on a **fresh native-Windows installation produced by
`Hermes-Setup.exe`**. From the installed/pinned Hermes checkout:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\apps\bootstrap-installer\smoke\windows-local-runtime-smoke.ps1
```

If the installer used a non-default `HERMES_HOME`, pass its install home
explicitly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\apps\bootstrap-installer\smoke\windows-local-runtime-smoke.ps1 -InstallHome 'D:\Hermes'
```

The script uses the installed `venv\Scripts\hermes.exe` and `python.exe` but
sets `HERMES_HOME` to a new temporary directory. It verifies the installed
`hermes local --help` surface, imports all shipped local-runtime modules,
resolves the SHA-256-pinned Windows AMD64 CUDA build plus its separate cudart
bundle, exercises complete → invalid cache transitions with tiny fixture files,
and checks separate stdout/stderr log paths. It never downloads a model or
llama.cpp archive and deletes its temporary home unless `-KeepSmokeHome` is
passed.

A successful run prints one JSON object with `"ok": true`; any failed invariant
exits non-zero with a PowerShell error.
