# Windows 离线安装包

在**能联网的电脑**上打一次包，把生成的 `EdgeSidekick-Setup-*-win-x64.exe` 拷到离线机器，双击即可安装。目标机**不需要** Python、Node.js，安装过程也**不访问网络**。

对话仍需要模型 API（公网，或内网 / 本机 Ollama 等）。安装本身与运行壳不依赖外网。

## 在开发机上构建

环境：Windows x64 · Node.js 18+ · 能访问 Python / npm / pip 镜像（脚本默认华为云、npmmirror、清华 PyPI）。

仓库根目录：

```powershell
.\scripts\build-windows.bat
```

或：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1
```

可选参数：

| 参数 | 作用 |
|---|---|
| `-SkipRuntime` | 复用已有 `packaging/windows/payload/python` |
| `-SkipUi` | 复用已有 `ui/dist` |
| `-SkipPlaywright` | 不打入 Chromium（体积更小，Agent 浏览器工具不可用） |
| `-Force` | 强制重下 Python 运行时 |

产物在 `desktop/dist/`：

- `EdgeSidekick-Setup-<version>-win-x64.exe` — NSIS 安装程序（推荐）
- `EdgeSidekick-<version>-win-x64.zip` — 解压即用的便携版

## 在离线电脑上安装

1. 拷贝 Setup `.exe`（U 盘 / 内网共享均可）
2. 双击安装，可选目录；**不需要管理员**（默认装到当前用户目录）
3. 开始菜单或桌面快捷方式启动 **EdgeSidekick**
4. 选工作区 → 设置里填模型 API（可指向内网）

配置与会话写在 `%APPDATA%\EdgeSidekick\`，卸载时默认保留。日志：`%APPDATA%\EdgeSidekick\logs\backend.log`。

便携 zip：解压后运行 `EdgeSidekick.exe`。

## 安装包里有什么

| 内容 | 说明 |
|---|---|
| Electron | 桌面窗口与侧栏实时浏览器 |
| Python 3.12 embeddable + 依赖 | 无需本机 Python |
| 已构建的 UI | `ui/dist` |
| 应用源码 | `main.py` + `src/metateam` |
| Playwright Chromium | Agent `browser_*`（可用 `-SkipPlaywright` 去掉） |

体积大约数百 MB（主要是 Chromium + Python）。

未签名时 Windows 可能提示 SmartScreen：选「更多信息」→「仍要运行」。可自行用代码签名证书签名 `desktop/dist` 里的安装包。
