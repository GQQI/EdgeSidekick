@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo  EdgeSidekick Desktop
echo  ----------------------------------------------------
echo.

REM ---- Resolve Python (project .venv; portable, no machine-specific conda) ----
call "%~dp0scripts\ensure-python.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo  Python: %SIDEKICK_PYTHON%

REM China-friendly mirrors (optional overrides still win)
if not defined ELECTRON_MIRROR set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
if not defined PLAYWRIGHT_DOWNLOAD_HOST set "PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright"

echo.
echo [0/4] Installing Python packages...
call "%~dp0scripts\install-python-deps.bat"
if errorlevel 1 (
  echo.
  echo Failed: Python dependency install
  echo Tip: delete .venv and re-run, or install uv / ensurepip.
  pause
  exit /b 1
)

echo.
echo [0b] Ensuring Playwright Chromium (agent browser tools^)...
"%SIDEKICK_PYTHON%" -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.launch(headless=True).close(); p.stop()" 1>nul 2>nul
if errorlevel 1 (
  echo      Chromium missing - downloading...
  "%SIDEKICK_PYTHON%" -m playwright install chromium
  if errorlevel 1 (
    echo Warning: playwright install chromium failed. Live Electron preview still works;
    echo          agent browser_* / screenshot mode may fail until this succeeds.
  )
) else (
  echo      Playwright Chromium OK
)

echo.
if not exist "desktop\node_modules\electron\dist\electron.exe" (
  echo [1/4] Installing desktop dependencies...
  pushd desktop
  call npm install
  if errorlevel 1 (
    echo.
    echo Failed: npm install in desktop\
    popd
    pause
    exit /b 1
  )
  if not exist "node_modules\electron\dist\electron.exe" (
    echo      Retrying Electron binary download...
    call node node_modules\electron\install.js
  )
  if not exist "node_modules\electron\dist\electron.exe" (
    echo      Node could not unzip Electron. Extracting the cached zip...
    powershell -NoProfile -ExecutionPolicy Bypass -Command " $cache = Join-Path $env:LOCALAPPDATA 'electron\Cache'; $zip = Get-ChildItem -Recurse -Filter 'electron-v*-win32-x64.zip' $cache -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (-not $zip) { Write-Host 'No cached Electron zip under %%LOCALAPPDATA%%\electron\Cache'; exit 1 }; $dest = Join-Path (Get-Location) 'node_modules\electron\dist'; if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }; New-Item -ItemType Directory -Force -Path $dest | Out-Null; Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory($zip.FullName, $dest); Set-Content -Encoding ascii -NoNewline -Path 'node_modules\electron\path.txt' -Value 'electron.exe'; Write-Host ('Extracted ' + $zip.Name) "
  )
  node -e "require('fs').writeFileSync('node_modules/electron/path.txt','electron.exe')"
  if not exist "node_modules\electron\dist\electron.exe" (
    echo.
    echo Still missing electron.exe. The cached zip was not extracted.
    popd
    pause
    exit /b 1
  )
  popd
) else (
  echo [1/4] Desktop deps OK
)

echo.
if not exist "ui\node_modules\" (
  echo [2/4] Installing UI dependencies...
  pushd ui
  call npm install
  if errorlevel 1 (
    echo.
    echo Failed: npm install in ui\
    popd
    pause
    exit /b 1
  )
  popd
) else (
  echo [2/4] UI node_modules OK
)

echo.
echo [3/4] Building UI...
pushd ui
call npm run build
if errorlevel 1 (
  echo.
  echo Standard UI build failed. Trying the local bundled fallback...
  if not exist "node_modules\@esbuild\win32-x64\esbuild.exe" (
    echo Failed: local fallback builder is unavailable in ui\
    popd
    pause
    exit /b 1
  )
  "node_modules\@esbuild\win32-x64\esbuild.exe" src\main.tsx --bundle --format=esm --target=es2020 --jsx=automatic --external:/app-bg.png --outfile=dist\assets\index-two-phase.js
  if errorlevel 1 (
    echo Failed: local fallback build in ui\
    popd
    pause
    exit /b 1
  )
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='dist\index.html'; $s=[IO.File]::ReadAllText($p); $s=[regex]::Replace($s,'/assets/index-[^/]+\.js','/assets/index-two-phase.js'); $s=[regex]::Replace($s,'/assets/index-[^/]+\.css','/assets/index-two-phase.css'); [IO.File]::WriteAllText($p,$s,[Text.UTF8Encoding]::new($false))"
  if errorlevel 1 (
    echo Failed: selecting fallback UI assets in ui\
    popd
    pause
    exit /b 1
  )
)
popd

echo.
echo [4/4] Launching Electron...
echo.
pushd desktop
node -e "require('fs').writeFileSync('node_modules/electron/path.txt','electron.exe')"
call npm start
set ERR=%ERRORLEVEL%
popd
if not "%ERR%"=="0" (
  echo.
  echo Electron exited with code %ERR%
  pause
)
exit /b %ERR%
