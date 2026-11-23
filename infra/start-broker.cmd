@echo off
rem ------------------------------------------------------------------------------------------
rem Start the FlyBot development MQTT broker (Mosquitto) in this window. Ctrl+C stops it.
rem
rem   infra\start-broker.cmd          LAN mode: ESP32 can connect on port 1883 (needs firewall rule)
rem   infra\start-broker.cmd local    localhost only (simulator, brain, dashboard on this laptop)
rem
rem Runs in the foreground: no Windows service, nothing left running in the background.
rem Mosquitto location: FLYBOT_MOSQUITTO_DIR, default D:\Tools\mosquitto.
rem Runtime state (password file, persistence, logs): .local\mosquitto\ (git-ignored).
rem ------------------------------------------------------------------------------------------
setlocal
if "%FLYBOT_MOSQUITTO_DIR%"=="" set "FLYBOT_MOSQUITTO_DIR=D:\Tools\mosquitto"
set "MOSQ=%FLYBOT_MOSQUITTO_DIR%\mosquitto.exe"
set "PY=infra\.venv\Scripts\python.exe"

rem Run from the repo root: all paths in the configs are relative to it.
pushd "%~dp0.."

set "CONF=infra\mosquitto\mosquitto.conf"
set "MODE=LAN (all interfaces)"
if /i "%~1"=="local" (
    set "CONF=infra\mosquitto\mosquitto-local.conf"
    set "MODE=localhost only"
)

if not exist "%MOSQ%" (
    echo ERROR: %MOSQ% not found. Set FLYBOT_MOSQUITTO_DIR or see infra\README.md.
    goto fail
)
if not exist ".local\mosquitto\passwd" (
    echo ERROR: no password file. Create it first:
    echo   %PY% infra\mosquitto\make_passwords.py
    goto fail
)

rem Warn if the ACL no longer matches the contract (needs the infra venv).
if exist "%PY%" (
    set "PYTHONPYCACHEPREFIX=D:\Tools\cache\pycache"
    "%PY%" infra\mosquitto\gen_acl.py --check >nul
    if errorlevel 1 (
        echo WARNING: infra\mosquitto\acl is out of date with docs\message-contract.md.
        echo          Run: %PY% infra\mosquitto\gen_acl.py
    )
)

if not exist ".local\mosquitto\data" mkdir ".local\mosquitto\data"
if not exist ".local\mosquitto\log" mkdir ".local\mosquitto\log"

echo Starting Mosquitto: %MODE%, ports 1883 (MQTT) and 9001 (WebSockets). Ctrl+C to stop.
"%MOSQ%" -c "%CONF%"
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:fail
popd
exit /b 1
