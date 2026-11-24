# Start the whole local FlyBot stack, each part in its own window (close a window to stop that part):
#   broker (localhost), brain (with the logger), the logger's read API, the dashboard,
#   and optionally the simulator's live view (-Sim).
#
#   powershell -ExecutionPolicy Bypass -File infra\start-all.ps1          broker + brain + logger API + dashboard
#   powershell -ExecutionPolicy Bypass -File infra\start-all.ps1 -Sim     ... plus the simulator live view
#   powershell -ExecutionPolicy Bypass -File infra\start-all.ps1 -DryRun  print the commands only
#
# Everything stays on D: (caches set below). The dashboard must be built once: cd dashboard; npm run build
# Then open http://localhost:3000 and sign in with DASHBOARD_PASSWORD from dashboard\.env.local.
param([switch]$Sim, [switch]$DryRun, [string]$Controller = "neural")

$Root = Split-Path -Parent $PSScriptRoot
$Cache = @{
    PYTHONPYCACHEPREFIX = "D:\Tools\cache\pycache"; PIP_CACHE_DIR = "D:\Tools\cache\pip";
    npm_config_cache = "D:\DevCache\npm"; NEXT_TELEMETRY_DISABLED = "1"; TMP = "D:\Tools\tmp"; TEMP = "D:\Tools\tmp"
}
$envSet = ($Cache.GetEnumerator() | ForEach-Object { "`$env:$($_.Key)='$($_.Value)'" }) -join "; "

$parts = @(
    @{ Name = "broker"; Dir = $Root; Cmd = "infra\start-broker.cmd local" },
    @{ Name = "brain"; Dir = "$Root\brain"; Cmd = ".venv\Scripts\python -m flybot_brain --controller $Controller" },
    @{ Name = "logger API"; Dir = "$Root\brain"; Cmd = ".venv\Scripts\python -m flybot_brain.logger api --port 8000" },
    @{ Name = "dashboard"; Dir = "$Root\dashboard"; Cmd = "npm run start" }
)
if ($Sim) { $parts += @{ Name = "simulator"; Dir = "$Root\sim"; Cmd = ".venv\Scripts\python -m flybot_sim live" } }

if (-not (Test-Path "$Root\dashboard\.next\BUILD_ID")) {
    Write-Warning "The dashboard is not built yet: cd dashboard; npm run build"
}
foreach ($p in $parts) {
    $line = "$envSet; `$host.UI.RawUI.WindowTitle = 'FlyBot $($p.Name)'; Set-Location '$($p.Dir)'; $($p.Cmd)"
    if ($DryRun) { Write-Output "[$($p.Name)] $($p.Dir)> $($p.Cmd)"; continue }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $line | Out-Null
    if ($p.Name -eq "broker") { Start-Sleep -Seconds 2 }        # the others connect to it
}
if (-not $DryRun) { Write-Output "Started: $($parts.Name -join ', '). Dashboard: http://localhost:3000" }
