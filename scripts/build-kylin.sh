#!/usr/bin/env bash
# Build 麒麟 / Linux offline packages: .deb + AppImage + portable tar.gz
#
# Must run on Linux (银河麒麟 / Ubuntu / WSL). Windows: scripts/build-kylin.bat
# (uses WSL or Docker). Do not cross-compile Python/Playwright for another CPU.
#
# Usage:
#   bash scripts/build-kylin.sh
#   bash scripts/build-kylin.sh --skip-runtime --skip-ui
#   ARCH=arm64 bash scripts/build-kylin.sh
set -euo pipefail

SKIP_RUNTIME=0
SKIP_UI=0
SKIP_PLAYWRIGHT=0
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-runtime) SKIP_RUNTIME=1 ;;
    --skip-ui) SKIP_UI=1 ;;
    --skip-playwright) SKIP_PLAYWRIGHT=1 ;;
    --force) FORCE=1 ;;
    --arch)
      ARCH="$2"
      shift
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACK="$ROOT/packaging/kylin"
CACHE="$PACK/cache"
PAYLOAD="$PACK/payload"
PY_DIR="$PAYLOAD/python"
PW_DIR="$PAYLOAD/ms-playwright"

# python-build-standalone: glibc 2.17+ (银河麒麟 V10 / Ubuntu 20.04+)
PY_RELEASE="${PY_RELEASE:-20251010}"
PY_VERSION="${PY_VERSION:-3.12.12}"

host_arch="$(uname -m)"
case "$host_arch" in
  x86_64|amd64) HOST_EB_ARCH=x64 ;;
  aarch64|arm64) HOST_EB_ARCH=arm64 ;;
  *)
    echo "Unsupported CPU: $host_arch (need x86_64 or aarch64)" >&2
    exit 1
    ;;
esac

ARCH="${ARCH:-$HOST_EB_ARCH}"
case "$ARCH" in
  x64|amd64|x86_64)
    ARCH=x64
    PY_TRIPLE="x86_64-unknown-linux-gnu"
    ;;
  arm64|aarch64)
    ARCH=arm64
    PY_TRIPLE="aarch64-unknown-linux-gnu"
    ;;
  *)
    echo "Unknown ARCH=$ARCH (use x64 or arm64)" >&2
    exit 1
    ;;
esac

if [[ "$ARCH" != "$HOST_EB_ARCH" ]]; then
  echo "Cannot cross-build $ARCH on $HOST_EB_ARCH." >&2
  echo "Pack on a matching 麒麟 / Linux machine (or WSL/Docker of that arch)." >&2
  exit 1
fi

PY_TGZ="cpython-${PY_VERSION}+${PY_RELEASE}-${PY_TRIPLE}-install_only_stripped.tar.gz"
PY_URLS=(
  "https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_TGZ}"
  "https://mirror.ghproxy.com/https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_TGZ}"
  "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_TGZ}"
)

PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/}"
export ELECTRON_BUILDER_BINARIES_MIRROR="${ELECTRON_BUILDER_BINARIES_MIRROR:-https://npmmirror.com/mirrors/electron-builder-binaries/}"
export PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright}"
export npm_config_registry="${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"

step() { printf '\n=== %s ===\n' "$1"; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    return 1
  fi
}

maybe_apt_tools() {
  local missing=()
  command -v curl >/dev/null 2>&1 || missing+=(curl)
  command -v tar >/dev/null 2>&1 || missing+=(tar)
  command -v dpkg >/dev/null 2>&1 || missing+=(dpkg)
  command -v fakeroot >/dev/null 2>&1 || missing+=(fakeroot)
  command -v xz >/dev/null 2>&1 || missing+=(xz-utils)
  if [[ ${#missing[@]} -eq 0 ]]; then
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
    step "Installing build tools (${missing[*]})"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      curl ca-certificates tar dpkg fakeroot xz-utils >/dev/null
    return 0
  fi
  echo "Install: ${missing[*]}  (e.g. sudo apt-get install -y curl tar dpkg fakeroot xz-utils)" >&2
  exit 1
}

download_first_ok() {
  local dest="$1"
  shift
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" && $(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest") -gt 1024 ]]; then
    echo "  cache hit: $dest"
    return 0
  fi
  local url
  for url in "$@"; do
    echo "  GET $url"
    if curl -fL --retry 2 --connect-timeout 20 -o "$dest" "$url"; then
      if [[ $(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest") -gt 1024 ]]; then
        return 0
      fi
    fi
    rm -f "$dest"
  done
  echo "Download failed." >&2
  exit 1
}

bundled_python_ok() {
  local py="$PY_DIR/bin/python3"
  [[ -x "$py" ]] || return 1
  "$py" -c "import fastapi, uvicorn, playwright" >/dev/null 2>&1
}

copy_tree() {
  local from="$1" to="$2"
  mkdir -p "$to"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' --exclude '.git' --exclude 'node_modules' \
      "$from"/ "$to"/
  else
    rm -rf "$to"
    mkdir -p "$to"
    tar -C "$from" --exclude='__pycache__' --exclude='.git' --exclude='node_modules' \
      -cf - . | tar -C "$to" -xf -
  fi
}

install_standalone_python() {
  step "Standalone CPython ${PY_VERSION} (${PY_TRIPLE})"
  local zip="$CACHE/$PY_TGZ"
  download_first_ok "$zip" "${PY_URLS[@]}"
  rm -rf "$PY_DIR"
  mkdir -p "$PAYLOAD"
  tar -xzf "$zip" -C "$PAYLOAD"
  if [[ ! -x "$PY_DIR/bin/python3" ]]; then
    echo "python/bin/python3 missing after extract" >&2
    exit 1
  fi
  local py="$PY_DIR/bin/python3"
  "$py" -m ensurepip --upgrade >/dev/null 2>&1 || true
  echo "  pip install -r requirements.txt"
  if ! "$py" -m pip install --no-warn-script-location -r "$ROOT/requirements.txt" -i "$PIP_INDEX"; then
    echo "  mirror failed, retrying default PyPI..."
    "$py" -m pip install --no-warn-script-location -r "$ROOT/requirements.txt"
  fi
  "$py" -c "import fastapi, uvicorn, playwright; print('python runtime ok')"
}

copy_app_payload() {
  step "Copying application payload"
  local src_dest="$PAYLOAD/src"
  rm -rf "$src_dest"
  copy_tree "$ROOT/src/metateam" "$src_dest/metateam"
  mkdir -p "$src_dest/data" "$src_dest/memory" "$src_dest/sessions" \
    "$src_dest/skills" "$src_dest/workspace"
  [[ -f "$ROOT/src/data/model.json.example" ]] && \
    cp "$ROOT/src/data/model.json.example" "$src_dest/data/model.json.example"
  [[ -f "$ROOT/src/memory/MEMORY.md" ]] && \
    cp "$ROOT/src/memory/MEMORY.md" "$src_dest/memory/MEMORY.md"
  if [[ -d "$ROOT/src/skills" ]]; then
    copy_tree "$ROOT/src/skills" "$src_dest/skills"
  fi
  cp "$ROOT/main.py" "$PAYLOAD/main.py"
  cp "$ROOT/requirements.txt" "$PAYLOAD/requirements.txt"
  if [[ ! -f "$ROOT/ui/dist/index.html" ]]; then
    echo "ui/dist missing" >&2
    exit 1
  fi
  rm -rf "$PAYLOAD/ui/dist"
  copy_tree "$ROOT/ui/dist" "$PAYLOAD/ui/dist"
}

install_playwright() {
  step "Bundling Playwright Chromium (linux)"
  mkdir -p "$PW_DIR"
  export PLAYWRIGHT_BROWSERS_PATH="$PW_DIR"
  if ! "$PY_DIR/bin/python3" -m playwright install chromium; then
    echo "  npmmirror failed, retrying Playwright default CDN..."
    unset PLAYWRIGHT_DOWNLOAD_HOST || true
    "$PY_DIR/bin/python3" -m playwright install chromium
  fi
  if ! find "$PW_DIR" -type f -name chrome | grep -q .; then
    echo "chromium chrome binary not found under $PW_DIR" >&2
    exit 1
  fi
}

# node_modules copied from Windows omit Linux optional binaries (rollup, electron).
ensure_linux_npm() {
  local dir="$1"
  local marker="$2"
  local label="$3"
  (
    cd "$dir"
    if [[ -e "$marker" ]]; then
      return 0
    fi
    echo "  ${label}: Linux native package missing, reinstalling npm dependencies"
    rm -rf node_modules
    npm install
  )
}

build_ui() {
  step "Building UI"
  need_cmd npm
  local rollup_pkg="node_modules/@rollup/rollup-linux-${ARCH}-gnu"
  ensure_linux_npm "$ROOT/ui" "$rollup_pkg" "ui"
  (
    cd "$ROOT/ui"
    npm run build
  )
}

build_packages() {
  step "electron-builder (deb + AppImage + tar.gz, $ARCH)"
  [[ -x "$PY_DIR/bin/python3" ]] || { echo "payload/python missing" >&2; exit 1; }
  [[ -f "$PAYLOAD/ui/dist/index.html" ]] || { echo "payload/ui/dist missing" >&2; exit 1; }
  need_cmd npx
  ensure_linux_npm "$ROOT/desktop" "node_modules/electron/dist/electron" "desktop"
  (
    cd "$ROOT/desktop"
    npx electron-builder --linux deb AppImage tar.gz --"$ARCH"
  )
}

# ---- main ----
echo
echo " EdgeSidekick 麒麟 / Linux offline packages"
echo " ------------------------------------"
echo " Repo: $ROOT"
echo " Arch: $ARCH ($PY_TRIPLE)"
echo

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script must run on Linux / 麒麟 / WSL." >&2
  echo "On Windows use:  scripts\\build-kylin.bat" >&2
  exit 1
fi

need_cmd node
need_cmd npm
maybe_apt_tools
mkdir -p "$CACHE" "$PAYLOAD"

if [[ "$SKIP_UI" -eq 0 ]]; then
  build_ui
elif [[ ! -f "$ROOT/ui/dist/index.html" ]]; then
  echo "ui/dist missing; omit --skip-ui" >&2
  exit 1
fi

if [[ "$SKIP_RUNTIME" -eq 1 ]]; then
  bundled_python_ok || { echo "payload/python not ready; omit --skip-runtime" >&2; exit 1; }
  step "Reusing bundled Python"
  echo "  $PY_DIR"
elif [[ "$FORCE" -eq 1 ]] || ! bundled_python_ok; then
  install_standalone_python
else
  step "Reusing bundled Python"
  echo "  $PY_DIR"
fi

copy_app_payload

if [[ "$SKIP_PLAYWRIGHT" -eq 0 ]]; then
  if [[ "$FORCE" -eq 1 ]] || ! find "$PW_DIR" -type f -name chrome 2>/dev/null | grep -q .; then
    install_playwright
  else
    step "Reusing Playwright Chromium"
  fi
fi

build_packages

echo
echo " Build finished."
echo " Copy one of these to the 麒麟 machine:"
find "$ROOT/desktop/dist" -maxdepth 1 -type f \( \
  -name '*.deb' -o -name '*.AppImage' -o -name '*.tar.gz' \
\) -printf '  %f  (%k KB)\n' 2>/dev/null || \
  ls -lh "$ROOT/desktop/dist"/*kylin* 2>/dev/null || true
echo
echo " Install .deb:   sudo dpkg -i desktop/dist/EdgeSidekick-*-kylin-*.deb && sudo apt-get install -f -y"
echo " AppImage:       chmod +x *.AppImage && ./EdgeSidekick-*-kylin-*.AppImage"
echo " Portable:       tar -xzf EdgeSidekick-*-kylin-*.tar.gz && ./EdgeSidekick-*/edgesidekick"
echo " Config/logs:    ~/.config/EdgeSidekick/"
echo
