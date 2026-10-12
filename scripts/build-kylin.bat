@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo.
echo  EdgeSidekick 麒麟 / Linux packager
echo  ------------------------------------
echo.

where wsl >nul 2>nul
if not errorlevel 1 (
  echo Using WSL...
  wsl.exe --cd "%CD%" -e bash scripts/build-kylin.sh %*
  exit /b %ERRORLEVEL%
)

where docker >nul 2>nul
if not errorlevel 1 (
  echo WSL not found. Using Docker ^(node:20-bookworm, glibc compatible with 麒麟 V10+^)...
  docker run --rm ^
    -v "%CD%":/src ^
    -w /src ^
    -e ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ ^
    -e ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/ ^
    -e PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright ^
    -e PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ^
    node:20-bookworm ^
    bash scripts/build-kylin.sh %*
  exit /b %ERRORLEVEL%
)

echo 麒麟安装包必须在 Linux 上构建（需要 Linux 版 Python 与 Chromium）。
echo.
echo 请任选其一后重新运行本脚本:
echo   1. 在银河麒麟 / Ubuntu 上:   bash scripts/build-kylin.sh
echo   2. Windows 安装 WSL:         wsl --install
echo   3. 安装 Docker Desktop
echo.
exit /b 1
