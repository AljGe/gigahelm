# -*- coding: utf-8 -*-
# Runs the full bench matrix elevated (one UAC prompt), then unattended
# except for the interactive power-source prompts of the battery phase.
# Log: tools/bench/results/bench.log
$ErrorActionPreference = "Stop"

$benchDir = $PSScriptRoot
$root = (Resolve-Path (Join-Path $benchDir "..\..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Error "python not found: $python"; exit 1 }

$results = Join-Path $benchDir "results"
New-Item -ItemType Directory -Force -Path $results | Out-Null
$log = Join-Path $results "bench.log"
$bench = Join-Path $benchDir "bench.py"

Write-Host "Elevating (one UAC prompt); bench runs in its own console window..."
$p = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru `
    -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "& '$python' -X utf8 '$bench' --phase all --full --resume --power both " +
        "*> '$log'; exit `$LASTEXITCODE"
    )

Write-Host ""
Write-Host "bench exit code: $($p.ExitCode)"
if (Test-Path $log) {
    Get-Content $log -Tail 50
    Write-Host ""
    Write-Host "LOG: $log"
}
exit $p.ExitCode
