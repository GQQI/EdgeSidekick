# 麒麟 / Linux 离线安装包

在 **Linux 或银河麒麟**（也可在 Windows 的 WSL / Docker）上打一次包，把生成的 `.deb` / AppImage / `tar.gz` 拷到离线麒麟机器安装。目标机**不需要**预装 Python、Node.js。

对话仍需要模型 API（公网，或内网 / 本机 Ollama 等）。安装本身与运行壳不依赖外网。

适配：**银河麒麟 V10 / 麒麟桌面**（Debian/Ubuntu 系，glibc ≥ 2.17）、通用 x86_64 / ARM64 Linux。

## 打包命令

### 在麒麟或 Linux 上

```bash
bash scripts/build-kylin.sh
```

飞腾 / 鲲鹏（ARM）：

```bash
ARCH=arm64 bash scripts/build-kylin.sh
```

构建机环境：Node.js 18+、能访问 pip / npm / GitHub（脚本默认清华 PyPI、npmmirror、gh 加速）。可选：`curl`、`dpkg`、`fakeroot`。

### 在 Windows 开发机上

仓库根目录：

```bat
.\scripts\build-kylin.bat
```

会依次尝试 **WSL** → **Docker**。没有这两者时会提示安装。不能在纯 Windows 上直接交叉编译 Linux Python / Chromium。

可选参数（sh / bat 均可）：

| 参数 | 作用 |
|---|---|
| `--skip-runtime` | 复用已有 `packaging/kylin/payload/python` |
| `--skip-ui` | 复用已有 `ui/dist` |
| `--skip-playwright` | 不打入 Chromium（体积更小，Agent 浏览器工具不可用） |
| `--force` | 强制重下 Python 运行时 |
| `--arch x64\|arm64` | 覆盖架构（必须与本机一致） |

产物在 `desktop/dist/`：

- `EdgeSidekick-<version>-kylin-<arch>.deb` — 麒麟 / Debian 安装包（推荐）
- `EdgeSidekick-<version>-kylin-<arch>.AppImage` — 免安装
- `EdgeSidekick-<version>-kylin-<arch>.tar.gz` — 解压即用

## 在离线麒麟电脑上安装

**deb（推荐）：**

```bash
sudo dpkg -i EdgeSidekick-*-kylin-*.deb
sudo apt-get install -f -y
```

开始菜单或命令 `edgesidekick` 启动。安装脚本会刷新 hicolor 图标缓存；若菜单仍无图标，注销再登录，或执行：

```bash
sudo gtk-update-icon-cache -f /usr/share/icons/hicolor
```


**AppImage：**

```bash
chmod +x EdgeSidekick-*-kylin-*.AppImage
./EdgeSidekick-*-kylin-*.AppImage
```

**便携 tar.gz：**

```bash
tar -xzf EdgeSidekick-*-kylin-*.tar.gz
./EdgeSidekick-*/edgesidekick
```

配置与会话：`~/.config/EdgeSidekick/`。日志：`~/.config/EdgeSidekick/logs/backend.log`。

## 安装包里有什么

| 内容 | 说明 |
|---|---|
| Electron | 桌面窗口与侧栏实时浏览器 |
| 独立 CPython 3.12 + 依赖 | 无需本机 Python |
| 已构建的 UI | `ui/dist` |
| 应用源码 | `main.py` + `src/metateam` |
| Playwright Chromium | Agent `browser_*`（可用 `--skip-playwright` 去掉） |

体积大约数百 MB（主要是 Chromium + Python）。

须在**与目标机相同的 CPU 架构**上打包（x86_64 与 ARM64 不能互打）。
