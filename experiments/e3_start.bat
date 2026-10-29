@echo off
rem ============================================================
rem E3 local launcher
rem   1. Ask for the tunnel URL printed by the E3 workflow run.
rem   2. Run connectivity checks (health endpoint + proxied HTTPS).
rem   3. If checks pass, open a dedicated Edge instance whose
rem      traffic goes through the local SOCKS5 relay.
rem
rem Requires: python on PATH, curl (bundled with Windows 10+).
rem The token is read invisibly and never written to disk.
rem ============================================================
setlocal enabledelayedexpansion

echo === E3 Edge browser proxy launcher ===

rem -- Stop any leftover client from a previous session so port 1080 is free.
taskkill /fi "WINDOWTITLE eq E3 SOCKS5 client*" /f >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*e3_edge_socks_client*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

set "URL="
set /p "URL=Step 1/3 - Enter tunnel URL (https://xxxx.trycloudflare.com): "
if not defined URL goto :err_nourl
echo %URL%| findstr /r /i "^https://[a-z0-9-]*\.trycloudflare\.com" >nul
if errorlevel 1 (
    echo [E3] ERROR: URL does not look like a trycloudflare HTTPS address.
    goto :end
)

set "TOKEN="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$s = Read-Host 'Step 2/3 - Enter E3 token' -AsSecureString; [System.Net.NetworkCredential]::new('', $s).Password"`) do set "TOKEN=%%i"
if not defined TOKEN (
    echo [E3] ERROR: No token entered.
    goto :end
)
powershell -NoProfile -Command "if ('%TOKEN%'.Length -lt 32) { exit 1 }"
if errorlevel 1 (
    echo [E3] ERROR: Token looks too short (expect 32+ chars).
    goto :end
)

echo [E3] Checking tunnel health endpoint...
curl -s --max-time 15 -o nul -w "%%{http_code}" "%URL%/health" > "%TEMP%\e3_health.txt" 2>nul
set /p HEALTH=<"%TEMP%\e3_health.txt"
echo [E3] /health -^> HTTP %HEALTH% (expect 200)
if not "%HEALTH%"=="200" (
    echo [E3] FAILED: tunnel is not reachable. Is the workflow run still holding?
    goto :end
)

echo [E3] Starting local SOCKS5 client on 127.0.0.1:1080 ...
set "E3_TUNNEL_TOKEN=%TOKEN%"
start "E3 SOCKS5 client" /min python "%~dp0e3_edge_socks_client.py" "%URL%"

set /a TRIES=0
:waitport
timeout /t 2 /nobreak >nul
python -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',1080))==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :portok
set /a TRIES+=1
if %TRIES% lss 10 goto :waitport
echo [E3] FAILED: local SOCKS5 client did not start. Check the client window.
goto :end

:portok
echo [E3] Testing HTTPS through the tunnel (api.ipify.org)...
curl -s --max-time 30 --socks5-hostname 127.0.0.1:1080 https://api.ipify.org > "%TEMP%\e3_ip.txt" 2>nul
if errorlevel 1 (
    echo [E3] FAILED: request through the tunnel did not complete.
    goto :stopclient
)
set /p EGRESS=<"%TEMP%\e3_ip.txt"
echo [E3] Egress IP through tunnel: %EGRESS%

curl -s --max-time 30 -o nul -w "%%{http_code}" --socks5-hostname 127.0.0.1:1080 https://example.com > "%TEMP%\e3_code.txt" 2>nul
set /p CODE=<"%TEMP%\e3_code.txt"
echo [E3] example.com -^> HTTP %CODE% (expect 200)
if not "%CODE%"=="200" (
    echo [E3] FAILED: HTTPS through the tunnel is not working.
    goto :stopclient
)

echo [E3] All connectivity checks PASSED.
echo [E3] Launching dedicated Edge instance through the proxy...
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" (
    echo [E3] ERROR: msedge.exe not found in the default locations.
    goto :stopclient
)
set "PROFILE=%TEMP%\Edge-E3-Proxy-Profile"
start "" "%EDGE%" --proxy-server=socks5://127.0.0.1:1080 --user-data-dir="%PROFILE%" --no-first-run https://api.ipify.org

echo.
echo === E3 session active ===
echo Edge window: traffic is proxied while it stays open.
echo api.ipify.org should show the runner egress IP: %EGRESS%
echo Keep this window open. Press any key here to stop the local client.
pause >nul

:stopclient
taskkill /fi "WINDOWTITLE eq E3 SOCKS5 client*" /f >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*e3_edge_socks_client*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
echo [E3] Local client stopped. Workflow run ends on its own hold timer.
goto :end

:err_nourl
echo [E3] ERROR: No URL entered.

:end
endlocal
