# 插件与插件市场

插件是可安装的第三方包，让智能体调用外部应用（GitHub、GitLab、Gmail……）。设计参考了 Dify 插件：一个包 = 清单（身份、凭据表单、工具声明）+ 代码；在「设置 → 插件」里从市场一键安装，或上传 zip、填 URL 安装。

## 使用

1. 打开「设置 → 插件 → 市场」，点「安装」。
2. 进入插件详情，填写凭据，点「保存并验证」。Gmail 这类 OAuth 插件填好 Client ID/Secret 后点「用 Google 授权」。
3. 之后所有对话（工作台和会话版群聊）里的智能体都能调用它的工具：
   - **读取类工具**（搜索、读取）直接执行，也可以在只读的判断阶段使用；
   - **写操作**（创建 Issue、评论、发送邮件）沿用应用原有的审批门，执行前弹窗确认。
4. 停用或卸载后，下一条消息起工具就会消失。

内置市场自带：

| 插件 | 能力 |
| --- | --- |
| GitHub | 搜索仓库 / Issue / PR，读取 Issue、PR 改动、文件、提交；创建 Issue、评论。支持 Enterprise |
| GitLab | 搜索项目，列出 / 读取 Issue 与 MR（含 diff 和流水线状态），读取文件；创建 Issue、评论。支持自建实例 |
| Gmail | Gmail 搜索语法查邮件，读取邮件 / 会话，标签；起草、发送（含回复同一会话） |

## 插件包格式

```
my-plugin/
  manifest.json   必需
  main.py         必需（或 manifest.entry 指定的文件）
  icon.svg        可选（svg/png/jpg/webp，≤96KB）
  README.md       可选，显示在详情页
```

打包：把文件夹压缩成 zip（清单在根目录或唯一的顶层文件夹里都可以），≤10MB。

### manifest.json

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
    {"name": "site", "label": "站点地址", "type": "text", "required": true, "placeholder": "https://xxx.atlassian.net"},
    {"name": "email", "label": "账号邮箱", "type": "text", "required": true},
    {"name": "token", "label": "API token", "type": "secret", "required": true, "url": "https://id.atlassian.com/manage-profile/security/api-tokens"}
  ],
  "tools": [
    {
      "name": "search_issues",
      "label": "搜索工单",
      "readonly": true,
      "description": "Search issues with JQL, e.g. 'project = APP AND status = \"In Progress\"'.",
      "parameters": {
        "type": "object",
        "properties": {"jql": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["jql"]
      }
    }
  ]
}
```

字段规则：

- `id`：2–32 位小写字母、数字或 `-`，字母开头。智能体看到的工具名是 `plugin_<id>_<tool>`（总长 ≤64）。
- `credentials[].type`：`secret`（加密保存，界面上不回显）、`text`、`select`（需要 `options`）。`default` 会在未填写时使用。
- `tools[].description` 必填——模型靠它决定什么时候调用。`parameters` 是 JSON Schema object。
- `tools[].readonly`：`true` 表示只读；`false`（默认）表示写操作，执行前需要用户确认。
- `oauth`（可选）：OAuth2 授权码流程，见 Gmail 插件。

```json
"oauth": {
  "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
  "token_url": "https://oauth2.googleapis.com/token",
  "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
  "client_id_field": "client_id",
  "client_secret_field": "client_secret",
  "token_field": "refresh_token",
  "extra_params": {"access_type": "offline", "prompt": "consent"},
  "label": "用 Google 授权"
}
```

回调地址是 `http://127.0.0.1:<端口>/api/plugins/oauth/callback`，需要在服务商那里登记（Google「桌面应用」类型的客户端允许任意本机端口）。

### main.py

每个工具一个同名函数，签名 `(params: dict, ctx: PluginContext)`，返回字符串或可 JSON 序列化的对象：

```python
from sidekick_plugin import PluginContext, ToolError

def search_issues(params: dict, ctx: PluginContext):
    site = ctx.require("site", "站点地址").rstrip("/")
    r = ctx.http.get(
        f"{site}/rest/api/3/search",
        params={"jql": params["jql"], "maxResults": params.get("limit") or 10},
        auth=(ctx.require("email"), ctx.require("token")),
    )
    ctx.raise_for_status(r, "搜索工单")   # 401/403/404 会变成可读的中文错误
    return [{"key": i["key"], "summary": i["fields"]["summary"]} for i in r.json()["issues"]]

def validate_credentials(ctx: PluginContext):   # 可选，「验证」按钮调用
    ...
    return "已连接 xxx"
```

- `ctx.credentials` / `ctx.credential(name)` / `ctx.require(name, label)`：读取凭据。
- `ctx.http`：预先配置好超时和 User-Agent 的 `httpx.Client`。
- 抛 `ToolError("…")`：消息会原样交给模型和用户。其他异常会被捕获并报告类型和信息。
- 返回值超过 24000 字符会被截断。

### 运行方式

每次工具调用都在**独立子进程**里执行（`src/metateam/plugins/runner.py`）：

- 凭据通过 stdin 传入，不进命令行参数或环境变量；
- 子进程只继承 PATH、代理、证书等必要环境变量，**拿不到**模型 API Key 等其他环境变量；
- 按 manifest 的 `timeout`（3–300 秒）超时终止；
- 插件里的 `print` 不会破坏返回结果。

## 安装与存储

- 插件文件：`<数据目录>/data/tenants/<用户>/plugins/<id>/`
- 状态与凭据：`<数据目录>/data/tenants/<用户>/plugins.json`（secret 字段用 Fernet 加密，密钥在 `data/.secret_key`）
- 升级时保留仍在新版本清单里的凭据；安装是先解压到临时目录再原子替换。
- zip 安装会拒绝路径穿越（`../`）、符号链接和解压后超过 40MB 的包。

## 自建市场源

在「设置 → 插件 → 市场源」添加（或设置环境变量 `SIDEKICK_PLUGIN_MARKETPLACE_URLS`，逗号分隔）一个返回如下 JSON 的地址：

```json
{
  "name": "团队插件",
  "plugins": [
    {
      "id": "jira",
      "name": "Jira",
      "version": "1.0.0",
      "description": "查询和创建 Jira 工单",
      "author": "Your team",
      "category": "开发协作",
      "tags": ["issue"],
      "icon_url": "https://plugins.example.com/jira.svg",
      "download_url": "https://plugins.example.com/jira-1.0.0.zip"
    }
  ]
}
```

- 索引缓存 10 分钟，界面上的「市场源」保存后会立即刷新。
- 远程条目不能覆盖内置插件的同名 id；下载的包 id 必须与条目一致，否则拒绝安装。
- 索引里的版本号高于已安装版本时，卡片上显示「更新」。
- 某个源不可用时只显示提示，不影响其他源和内置市场。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/plugins/marketplace?refresh=` | 市场目录（含安装状态） |
| PUT | `/api/plugins/marketplace/sources` | 保存市场源 `{sources: []}` |
| GET | `/api/plugins` | 已安装插件 |
| POST | `/api/plugins/install` | `{id}` 从市场安装，或 `{url}` 从 zip 地址安装 |
| POST | `/api/plugins/upload` | multipart `file` 上传 zip |
| GET / PATCH / DELETE | `/api/plugins/{id}` | 详情 / `{enabled}` / 卸载 |
| PUT | `/api/plugins/{id}/credentials` | `{values, clear, verify}`；secret 留空表示不修改 |
| POST | `/api/plugins/{id}/verify` | 调用插件的 `validate_credentials` |
| POST | `/api/plugins/{id}/oauth/start` | 返回授权地址 |
| GET | `/api/plugins/oauth/callback` | OAuth 回调（无需令牌，靠一次性 state 绑定用户） |
| POST | `/api/plugins/{id}/tools/{tool}/invoke` | 在对话之外直接调用一个工具 `{params}` |

## 测试

```bash
python -m pytest tests -q
```

`tests/test_plugins.py` 用 httpx MockTransport 模拟 GitHub / GitLab / Gmail / OAuth / 远程市场，并用真实子进程测试运行器（超时、异常、凭据校验）。
