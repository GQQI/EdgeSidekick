#Requires -Version 5.1
<#
.SYNOPSIS
  Build a fully offline Windows installer (NSIS .exe) and portable zip.

.DESCRIPTION
  Run on an online build PC. The output Setup.exe can be copied to an air-gapped
  machine and installed without Python, Node, or internet.

  Bundles: Electron shell + embeddable CPython + pip packages + Playwright Chromium
  + built UI + application source.

.PARAMETER SkipRuntime
  Reuse packaging/windows/payload/python if it already imports fastapi.

.PARAMETER SkipUi
  Reuse existing ui/dist instead of running npm run build.

.PARAMETER SkipPlaywright
  Do not download Chromium (agent browser_* tools will not work offline).

.PARAMETER Force
  Rebuild the bundled Python runtime even if it already exists.
#>
param(
  [switch]$SkipRuntime,
  [switch]$SkipUi,
  [switch]$SkipPlaywright,
  [switch]$Force
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# Native tools (python/pip/npm) write warnings to stderr. Windows PowerShell 5.1
# treats that as a terminating error when ErrorActionPreference=Stop.
$ErrorActionPreference = "Continue"
$PackDir = Join-Path $RepoRoot "packaging\windows"
$CacheDir = Join-Path $PackDir "cache"
$Payload = Join-Path $PackDir "payload"
$PythonDir = Join-Path $Payload "python"
$PwDir = Join-Path $Payload "ms-playwright"

$PythonVersion = "3.12.10"
$EmbedZipName = "python-$PythonVersion-embed-amd64.zip"

$PythonUrls = @(
  "https://mirrors.huaweicloud.com/python/$PythonVersion/$EmbedZipName",
  "https://www.python.org/ftp/python/$PythonVersion/$EmbedZipName"
)
$GetPipUrls = @(
  "https://bootstrap.pypa.io/get-pip.py",
  "https://mirrors.aliyun.com/pypi/get-pip.py"
)

function Write-Step([string]$msg) {
  Write-Host ""
  Write-Host "=== $msg ===" -ForegroundColor Cyan
}

function Ensure-Dir([string]$path) {
  if (-not (Test-Path -LiteralPath $path)) {
    New-Item -ItemType Directory -Path $path | Out-Null
  }
}

function Get-PipIndex {
  if ($env:PIP_INDEX_URL) { return $env:PIP_INDEX_URL }
  return "https://pypi.tuna.tsinghua.edu.cn/simple"
}

function Get-HostPipPython {
  $candidates = @()
  if ($env:SIDEKICK_PYTHON) { $candidates += $env:SIDEKICK_PYTHON }
  $candidates += (Join-Path $RepoRoot ".venv\Scripts\python.exe")
  $cmd = Get-Command py -ErrorAction SilentlyContinue
  if ($cmd) { $candidates += $cmd.Source }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $candidates += $cmd.Source }
  foreach ($p in $candidates) {
    if (-not $p) { continue }
    if ($p -eq "py" -or $p -eq "python") { }
    elseif (-not (Test-Path -LiteralPath $p)) { continue }
    $code = Invoke-QuietExitCode { & $p -m pip --version }
    if ($code -eq 0) { return $p }
  }
  return $null
}

function Invoke-PipInstall {
  param(
    [string]$EmbedPy,
    [string]$Requirements
  )
  $index = Get-PipIndex
  $extraIndex = @("-i", $index)

  if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "  uv pip install --python (bundled) -r requirements.txt"
    & uv pip install --python $EmbedPy -r $Requirements --index-url $index
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "  uv failed, trying host pip..." -ForegroundColor Yellow
  }

  $hostPy = Get-HostPipPython
  if ($hostPy) {
    Write-Host "  host pip --python (bundled) via $hostPy"
    & $hostPy -m pip install --python $EmbedPy --no-warn-script-location -r $Requirements @extraIndex
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "  mirror failed, retrying PyPI with host pip..." -ForegroundColor Yellow
    & $hostPy -m pip install --python $EmbedPy --no-warn-script-location -r $Requirements
    if ($LASTEXITCODE -eq 0) { return $true }
  }

  Write-Host "  bootstrapping pip into embeddable Python (official get-pip)..."
  $getPip = Join-Path $CacheDir "get-pip-pypa.py"
  Download-FirstOk -Urls $GetPipUrls -OutFile $getPip
  & $EmbedPy $getPip --no-warn-script-location
  if ($LASTEXITCODE -ne 0) { return $false }
  Write-Host "  bundled python -m pip install -r requirements.txt"
  & $EmbedPy -m pip install --no-warn-script-location -r $Requirements @extraIndex
  if ($LASTEXITCODE -eq 0) { return $true }
  & $EmbedPy -m pip install --no-warn-script-location -r $Requirements
  return ($LASTEXITCODE -eq 0)
}

function Download-FirstOk {
  param(
    [string[]]$Urls,
    [string]$OutFile
  )
  Ensure-Dir (Split-Path -Parent $OutFile)
  foreach ($url in $Urls) {
    Write-Host "  GET $url"
    try {
      Invoke-WebRequest -Uri $url -OutFile $OutFile -UseBasicParsing
      if ((Get-Item $OutFile).Length -gt 1024) { return }
    } catch {
      Write-Host "  failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
  }
  throw "Download failed. Tried: $($Urls -join ', ')"
}

function Invoke-RobocopySafe {
  param(
    [string]$From,
    [string]$To,
    [string[]]$ExtraArgs = @()
  )
  Ensure-Dir $To
  $args = @($From, $To, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/nc", "/ns", "/np", "/XD", "__pycache__", ".git", "node_modules") + $ExtraArgs
  & robocopy @args | Out-Null
  if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed ($LASTEXITCODE): $From -> $To"
  }
}

function Invoke-QuietExitCode {
  param([scriptblock]$Block)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Block 2>$null | Out-Null
    if ($null -eq $LASTEXITCODE) { return 0 }
    return $LASTEXITCODE
  } catch {
    return 1
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Test-BundledPythonOk {
  $py = Join-Path $PythonDir "python.exe"
  if (-not (Test-Path -LiteralPath $py)) { return $false }
  # pydantic_core is the actual compiled DLL that tends to break (stale
  # cached wheel, antivirus stripping a file, ABI mismatch) - import it
  # explicitly instead of only the pure-Python fastapi/uvicorn wrappers,
  # which can appear to "pass" a shallow check while the real import chain
  # is broken (this is exactly what caused installed .exe to crash with
  # "DLL load failed while importing _pydantic_core").
  $savedHome = $env:PYTHONHOME
  $savedPath = $env:PYTHONPATH
  Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
  Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
  try {
    $code = Invoke-QuietExitCode { & $py -c "import encodings, fastapi, uvicorn, playwright, pydantic_core" }
    return ($code -eq 0)
  } finally {
    if ($null -ne $savedHome) { $env:PYTHONHOME = $savedHome }
    if ($null -ne $savedPath) { $env:PYTHONPATH = $savedPath }
  }
}

function Get-EmbedStdlibZip {
  Get-ChildItem -LiteralPath $PythonDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^python\d+\.zip$' } |
    Select-Object -First 1
}

function Write-EmbedPth {
  $zip = Get-EmbedStdlibZip
  if (-not $zip) { throw "pythonXX.zip not found in $PythonDir" }
  $pthPath = Join-Path $PythonDir ([IO.Path]::ChangeExtension($zip.Name, "._pth"))
  # Isolated-mode search path. "Lib" is the unpacked stdlib so encodings is
  # found even if pythonXX.zip is stripped by antivirus after install.
  $content = (@(
    "Lib"
    $zip.Name
    "Lib\site-packages"
    "import site"
  ) -join "`r`n") + "`r`n"
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($pthPath, $content, $utf8)
  Write-Host ("  wrote {0}" -f (Split-Path -Leaf $pthPath))
}

function Expand-EmbedStdlib {
  $zip = Get-EmbedStdlibZip
  if (-not $zip) { throw "pythonXX.zip not found in $PythonDir" }
  $lib = Join-Path $PythonDir "Lib"
  Ensure-Dir $lib
  $marker = Join-Path $lib "encodings"
  if (Test-Path -LiteralPath $marker) {
    Write-Host "  stdlib already unpacked: $marker"
    return
  }
  Write-Host ("  extracting {0} into Lib (stdlib on disk, not zip-only)" -f $zip.Name)
  $tmp = Join-Path $CacheDir "python-stdlib-extract"
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
  New-Item -ItemType Directory -Path $tmp | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zip.FullName, $tmp)
  Invoke-RobocopySafe -From $tmp -To $lib
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  if (-not (Test-Path -LiteralPath $marker)) {
    throw ("failed to extract encodings from {0}" -f $zip.Name)
  }
}

function Initialize-EmbeddablePythonLayout {
  Write-EmbedPth
  Expand-EmbedStdlib
  Ensure-Dir (Join-Path $PythonDir "Lib\site-packages")
}

function Install-EmbeddablePython {
  Write-Step "Preparing embeddable Python $PythonVersion"
  $zip = Join-Path $CacheDir $EmbedZipName
  if (-not (Test-Path -LiteralPath $zip) -or (Get-Item $zip).Length -lt 1024) {
    Download-FirstOk -Urls $PythonUrls -OutFile $zip
  } else {
    Write-Host "  cache hit: $zip"
  }

  if (Test-Path -LiteralPath $PythonDir) {
    Remove-Item -LiteralPath $PythonDir -Recurse -Force
  }
  Ensure-Dir $PythonDir
  Expand-Archive -LiteralPath $zip -DestinationPath $PythonDir -Force
  Initialize-EmbeddablePythonLayout

  $py = Join-Path $PythonDir "python.exe"
  $req = Join-Path $RepoRoot "requirements.txt"
  $ok = Invoke-PipInstall -EmbedPy $py -Requirements $req
  if (-not $ok) { throw "failed to install Python packages into bundled runtime" }

  # Explicitly test pydantic_core (the compiled DLL most likely to be broken
  # by a stale pip cache, an ABI/arch mismatch, or antivirus stripping a
  # file during extraction) and print where it actually loaded from, so a
  # future "DLL load failed" only shows up here — at build time — instead of
  # after install on the target machine.
  & $py -c "import fastapi, uvicorn, playwright, pydantic_core; print('python runtime ok'); print('pydantic_core:', pydantic_core.__file__)"
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  pydantic_core import failed — forcing a clean reinstall (no cache) and retrying..." -ForegroundColor Yellow
    & $py -m pip uninstall -y pydantic pydantic-core 2>$null | Out-Null
    & $py -m pip install --no-cache-dir --force-reinstall -r $req --index-url (Get-PipIndex)
    & $py -c "import fastapi, uvicorn, playwright, pydantic_core; print('python runtime ok (after clean reinstall)')"
    if ($LASTEXITCODE -ne 0) {
      throw ("bundled Python import check failed even after a clean reinstall. " +
        "This is almost always antivirus quarantining/stripping a DLL under " +
        "'$PythonDir\Lib\site-packages\pydantic_core' during extraction, or a 32/64-bit " +
        "mismatch. Add an antivirus exclusion for '$PythonDir' and re-run with -Force.")
    }
  }
}

function Copy-AppPayload {
  Write-Step "Copying application payload"
  $srcDest = Join-Path $Payload "src"
  if (Test-Path -LiteralPath $srcDest) {
    Remove-Item -LiteralPath $srcDest -Recurse -Force
  }
  Invoke-RobocopySafe -From (Join-Path $RepoRoot "src\metateam") -To (Join-Path $srcDest "metateam")

  Ensure-Dir (Join-Path $srcDest "data")
  Ensure-Dir (Join-Path $srcDest "memory")
  Ensure-Dir (Join-Path $srcDest "sessions")
  Ensure-Dir (Join-Path $srcDest "skills")
  Ensure-Dir (Join-Path $srcDest "workspace")

  $example = Join-Path $RepoRoot "src\data\model.json.example"
  if (Test-Path -LiteralPath $example) {
    Copy-Item -LiteralPath $example -Destination (Join-Path $srcDest "data\model.json.example") -Force
  }
  $mem = Join-Path $RepoRoot "src\memory\MEMORY.md"
  if (Test-Path -LiteralPath $mem) {
    Copy-Item -LiteralPath $mem -Destination (Join-Path $srcDest "memory\MEMORY.md") -Force
  }
  $skillsSrc = Join-Path $RepoRoot "src\skills"
  if (Test-Path -LiteralPath $skillsSrc) {
    Invoke-RobocopySafe -From $skillsSrc -To (Join-Path $srcDest "skills")
  }

  Copy-Item -LiteralPath (Join-Path $RepoRoot "main.py") -Destination (Join-Path $Payload "main.py") -Force
  Copy-Item -LiteralPath (Join-Path $RepoRoot "requirements.txt") -Destination (Join-Path $Payload "requirements.txt") -Force

  $uiDist = Join-Path $RepoRoot "ui\dist"
  if (-not (Test-Path -LiteralPath $uiDist)) {
    throw "ui/dist missing. UI build did not run (or failed)."
  }
  $uiDest = Join-Path $Payload "ui\dist"
  if (Test-Path -LiteralPath $uiDest) {
    Remove-Item -LiteralPath $uiDest -Recurse -Force
  }
  Invoke-RobocopySafe -From $uiDist -To $uiDest
}

function Install-PlaywrightChromium {
  Write-Step "Bundling Playwright Chromium"
  $py = Join-Path $PythonDir "python.exe"
  Ensure-Dir $PwDir
  $env:PLAYWRIGHT_BROWSERS_PATH = $PwDir
  if (-not $env:PLAYWRIGHT_DOWNLOAD_HOST) {
    $env:PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright"
  }
  & $py -m playwright install chromium
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  npmmirror failed, retrying default Playwright CDN..." -ForegroundColor Yellow
    Remove-Item Env:PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
    & $py -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed" }
  }
  $chrome = Get-ChildItem -LiteralPath $PwDir -Recurse -Filter "chrome.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $chrome) { throw "chrome.exe not found under $PwDir" }
  Write-Host "  $($chrome.FullName)"
}

function Build-Ui {
  Write-Step "Building UI"
  $ui = Join-Path $RepoRoot "ui"
  Push-Location $ui
  try {
    if (-not (Test-Path -LiteralPath (Join-Path $ui "node_modules"))) {
      Write-Host "  npm install (ui)"
      npm install
      if ($LASTEXITCODE -ne 0) { throw "npm install failed in ui/" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed in ui/" }
  } finally {
    Pop-Location
  }
}

function Stop-SidekickBuildLockers {
  # Product exe name is 3 CJK chars; built from codepoints so this file stays ASCII-safe for PS 5.1.
  $productProc = -join @([char]0x9A6D, [char]0x5929, [char]0x72FC)
  $names = @("electron", "EdgeSidekick", "app-builder", $productProc)
  foreach ($name in $names) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
      Write-Host ("  stopping PID {0} ({1}) to unlock dist files" -f $_.Id, $_.ProcessName)
      Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
  }
}

function Remove-TreeRetry([string]$path) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  for ($i = 0; $i -lt 6; $i++) {
    try {
      Get-ChildItem -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $_.Attributes = "Normal"
      }
      Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
      return
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  Write-Host "  warning: could not fully delete $path (file lock)" -ForegroundColor Yellow
}

function Test-PathHasNonAscii([string]$path) {
  foreach ($ch in $path.ToCharArray()) {
    if ([int][char]$ch -gt 127) { return $true }
  }
  return $false
}

function Build-Installer {
  Write-Step "electron-builder (NSIS + zip)"
  $desktop = Join-Path $RepoRoot "desktop"
  $dist = Join-Path $desktop "dist"
  if (-not (Test-Path -LiteralPath (Join-Path $Payload "python\python.exe"))) {
    throw "payload/python missing - runtime was not prepared"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $Payload "ui\dist\index.html"))) {
    throw "payload/ui/dist missing - UI was not copied"
  }
  Push-Location $desktop
  try {
    if (-not $env:ELECTRON_MIRROR) {
      $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    }
    if (-not $env:ELECTRON_BUILDER_BINARIES_MIRROR) {
      $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
    }
    # Skip Windows code-sign discovery; rcedit/signing often EPERM-locks d3dcompiler_47.dll.
    if (-not $env:CSC_IDENTITY_AUTO_DISCOVERY) {
      $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $desktop "node_modules\electron-builder"))) {
      Write-Host "  npm install (desktop / electron-builder)"
      npm install
      if ($LASTEXITCODE -ne 0) { throw "npm install failed in desktop/" }
    }

    # Unpack Electron under an ASCII path. Defender + a Chinese repo path commonly
    # yields: EPERM open '...dist\win-unpacked.tmp\d3dcompiler_47.dll'
    $packOut = $dist
    if (Test-PathHasNonAscii $dist) {
      $packOut = Join-Path $env:LOCALAPPDATA "edgesidekick-electron-dist"
      Write-Host "  output dir (ASCII to avoid EPERM on DLL extract): $packOut"
    }

    Stop-SidekickBuildLockers
    foreach ($leaf in @("win-unpacked", "win-unpacked.tmp", "win-ia32-unpacked", "win-ia32-unpacked.tmp")) {
      Remove-TreeRetry (Join-Path $packOut $leaf)
      if ($packOut -ne $dist) { Remove-TreeRetry (Join-Path $dist $leaf) }
    }

    $ok = $false
    $maxAttempts = 3
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
      Write-Host "  electron-builder --win (attempt $attempt/$maxAttempts)"
      if ($packOut -ne $dist) {
        npx electron-builder --win "--config.directories.output=$packOut"
      } else {
        npx electron-builder --win
      }
      if ($LASTEXITCODE -eq 0) {
        $ok = $true
        break
      }
      Write-Host "  electron-builder failed ($LASTEXITCODE); retrying after unlocking dist..." -ForegroundColor Yellow
      Stop-SidekickBuildLockers
      Start-Sleep -Seconds (3 * $attempt)
      foreach ($leaf in @("win-unpacked", "win-unpacked.tmp")) {
        Remove-TreeRetry (Join-Path $packOut $leaf)
      }
    }
    if (-not $ok) {
      throw ("electron-builder failed after {0} attempts. EPERM on d3dcompiler_47.dll is usually Defender/360 locking the unpacked DLL. Close Electron if it is running, add an AV exclusion for '{1}' and '{2}', then re-run scripts\build-windows.bat" -f $maxAttempts, $packOut, $desktop)
    }

    if ($packOut -ne $dist) {
      Ensure-Dir $dist
      Get-ChildItem -LiteralPath $packOut -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dist $_.Name) -Force
      }
    }
  } finally {
    Pop-Location
  }
}

# ---- main ----
Write-Host ""
Write-Host " EdgeSidekick Windows offline installer"
Write-Host " ----------------------------------"
Write-Host " Repo: $RepoRoot"
Ensure-Dir $CacheDir
Ensure-Dir $Payload

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw "Node.js is required on the build machine (not on the target PC)."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm is required on the build machine."
}

if (-not $SkipUi) {
  Build-Ui
} elseif (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "ui\dist\index.html"))) {
  throw "ui/dist missing; omit -SkipUi"
}

$needRuntime = $Force -or -not (Test-BundledPythonOk)
if ($SkipRuntime -and -not (Test-BundledPythonOk)) {
  throw "payload/python is not ready; omit -SkipRuntime or pass -Force"
}
if (-not $SkipRuntime -and $needRuntime) {
  Install-EmbeddablePython
} else {
  Write-Step "Reusing bundled Python"
  Write-Host "  $PythonDir"
  Initialize-EmbeddablePythonLayout
  if (-not (Test-BundledPythonOk)) {
    throw "payload/python failed import check after layout fix; omit -SkipRuntime or pass -Force"
  }
}

Copy-AppPayload

if (-not $SkipPlaywright) {
  $chrome = Get-ChildItem -LiteralPath $PwDir -Recurse -Filter "chrome.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($Force -or -not $chrome) {
    Install-PlaywrightChromium
  } else {
    Write-Step "Reusing Playwright Chromium"
    Write-Host "  $($chrome.FullName)"
  }
}

Build-Installer

$dist = Join-Path $RepoRoot "desktop\dist"
Write-Host ""
Write-Host " Build finished." -ForegroundColor Green
Write-Host " Copy one of these to the offline PC:"
Get-ChildItem -LiteralPath $dist -File | Where-Object {
  $_.Extension -in ".exe", ".zip"
} | ForEach-Object {
  Write-Host ("  {0,-12} {1:N1} MB  {2}" -f $_.Extension, ($_.Length / 1MB), $_.FullName)
}
Write-Host ""
Write-Host " Install: double-click the Setup .exe (no Python/Node/network required)."
Write-Host " Chat still needs a model API (cloud or LAN Ollama) unless you use a local endpoint."
