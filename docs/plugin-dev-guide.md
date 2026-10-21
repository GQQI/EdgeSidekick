# Sidekick 第三方插件开发说明

本文说明如何开发、打包并上传 **Sidekick 第三方插件**（设置 → 插件）。插件是独立的应用包（`manifest.json` + `main.py`），与 **MCP**（设置 → MCP）无关。

## 与 MCP 的区别

| | 插件（本页） | MCP |
| --- | --- | --- |
| 入口 | 设置 → 插件 | 设置 → MCP |
| 形态 | zip 包：manifest + Python 入口 | 外部 MCP Server（stdio / HTTP / SSE） |
| 安装 | 市场 / 上传 / URL | 开源目录一键写入，或手动配置 |

## 快速开始

1. 在「设置 → 插件」点击「下载开发模板」，得到 `hello-plugin.zip`。
2. 修改 `manifest.json` 的 `id`、`name`、`tools`；在 `main.py` 实现与工具同名的函数。
3. 将文件夹打成 zip（≤10MB）后上传，或通过「从 URL 安装」提供 zip 地址。

也可请求 `GET /api/plugins/template.zip` 获取模板；本说明对应 `GET /api/plugins/guide`。

## 包结构

```
my-plugin/
  manifest.json   必需
  main.py         必需（或由 manifest.entry 指定）
  icon.svg        可选（svg/png/jpg/webp，≤96KB）
  README.md       可选（详情页展示）
```

压缩时，清单可位于 zip 根目录，或唯一的顶层文件夹内。

## manifest.json

```json
{
  "id": "jira",
  "name": "Jira",
  "version": "1.0.0",
  "author": "Your team",
  "description": "查询和创建 Jira 工单",
  "category": "开发协作",
  "tags": ["issue"],
  "icon": "icon.svg",
  "entry": "main.py",
  "timeout": 30,
  "network": ["your-domain.atlassian.net"],
  "credentials": [
    {"name": "site", "label": "站点地址", "type": "text", "required": true},
    {"name": "token", "label": "API token", "type": "secret", "required": true}
  ],
  "tools": [
    {
      "name": "search_issues",
      "label": "搜索工单",
      "readonly": true,
      "description": "Search issues with JQL.",
      "parameters": {
        "type": "object",
        "properties": {"jql": {"type": "string"}},
        "required": ["jql"]
      }
    }
  ]
}
```

要点：

- `id`：2–32 位小写字母、数字或 `-`，字母开头。工具名形如 `plugin_<id>_<tool>`（总长 ≤64）。
- `credentials[].type`：`secret` / `text` / `select`。
- `tools[].description` 必填；`parameters` 为 JSON Schema object。
- `tools[].readonly`：`true` 为只读；`false`（默认）为写操作，执行前需确认。
- `network` 与 `credentials` 均为空时视为离线插件，安装后即可用。

可选 `oauth` 字段用于授权码流程（参见内置 Gmail 集成）。回调地址为 `http://127.0.0.1:<端口>/api/plugins/oauth/callback`。

## main.py

每个工具对应同名函数，签名 `(params: dict, ctx: PluginContext)`，返回字符串或可 JSON 序列化对象：

```python
from sidekick_plugin import PluginContext, ToolError

def search_issues(params: dict, ctx: PluginContext):
    site = ctx.require("site", "站点地址").rstrip("/")
    r = ctx.http.get(f"{site}/rest/api/3/search", params={"jql": params["jql"]})
    ctx.raise_for_status(r, "搜索工单")
    return r.json()

def validate_credentials(ctx: PluginContext):
    return "已连接"
```

- `ctx.credentials` / `ctx.credential` / `ctx.require`：读取凭据。
- `ctx.http`：预配置超时与 User-Agent 的 `httpx.Client`。
- 抛出 `ToolError("…")` 时消息会交给模型与用户；其他异常会被捕获并报告。
- 返回值超过约 24000 字符会被截断。

## 运行与安全

每次工具调用在独立子进程执行：凭据经 stdin 传入；子进程不继承模型 API Key 等敏感环境变量；按 `timeout`（3–300 秒）超时终止。

## 安装与存储

- 文件：`<数据目录>/data/tenants/<用户>/plugins/<id>/`
- 状态与凭据：`plugins.json`（secret 字段加密）
- zip 安装拒绝路径穿越、符号链接及解压后过大的包

## 自建市场源

在「设置 → 插件 → 市场源」添加索引 URL（或环境变量 `SIDEKICK_PLUGIN_MARKETPLACE_URLS`）。索引 JSON 形如：

```json
{
  "name": "团队插件",
  "plugins": [
    {
      "id": "jira",
      "name": "Jira",
      "version": "1.0.0",
      "description": "查询和创建 Jira 工单",
      "download_url": "https://plugins.example.com/jira-1.0.0.zip"
    }
  ]
}
```

更完整的字段说明与 HTTP API 见 [plugins.md](plugins.md)。上传步骤摘要见 [plugin-upload.md](plugin-upload.md)。
