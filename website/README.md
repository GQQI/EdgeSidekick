# EdgeSidekick 官网（本地静态站）

路径：`website/`（相对仓库根目录）。

## 打开方式

**方式 A — 双击打开（最简单）**

1. 用资源管理器打开本目录
2. 双击 `index.html`（默认浏览器）
> 部分浏览器对本地 `file://` 下相对路径的 video 可能有限制；若 mp4 不播，用方式 B。

**方式 B — 本地静态服务（推荐）**

在仓库根目录或本目录执行：

```powershell
# PowerShell（在 website 目录）
cd path\to\Sidekick\website
python -m http.server 8080
```

浏览器打开：

- 官网：http://127.0.0.1:8080/
- 使用手册：http://127.0.0.1:8080/docs/user-guide.zh.html

## 文件结构

```
website/
  index.html      # 多段营销官网（中文主、可切英文；浅/深色）
  promo.html      # 可选演示页
  docs/           # 使用手册 zh/en
  css/style.css
  css/promo.css
  js/main.js
  js/promo.js
  assets/         # logo + 截图
  README.md
```

介绍片：`assets/edgesidekick-intro.mp4`（1080p，约 46s。工作台、会话版与 MCP 为真实界面截图，并介绍工具调用和本地架构。由 `scripts/make-intro-mp4.py` 生成）

## 说明

- 样式：AgentFrame 蓝白（浅色主视觉，可选深色）
- Logo：`assets/favicon.svg`（来自 `ui/public/favicon.svg`）
- 截图：浅色清爽 / 深色沉浸（`gallery-workbench-light/dark.png`）
- 无外部付费 CDN；系统字体

## 双语 / i18n

- 默认语言：中文（`zh`）
- 右上角 **EN / 中文** 切换；`localStorage` 键：`esk-lang`
- `website/js/main.js` 与 `website/js/promo.js` 共用同一键，官网与宣传页语言联动
- 可见文案通过 `data-i18n` + 字典（`zh` / `en`）渲染；切换会更新标题与 meta description

## 界面预览图

两张真实 UI 截图（工作台浅色 / 深色）：

- `assets/gallery-workbench-light.png`
- `assets/gallery-workbench-dark.png`

同步副本：`docs/screenshots/gallery-workbench-*.png`

