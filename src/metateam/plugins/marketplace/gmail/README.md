# Gmail 插件

让智能体帮你查邮件、读会话、整理要点，并起草或发送回复（发送前一定会弹出确认）。

## 配置（约 5 分钟）

Sidekick **不附带**共享的 Google OAuth 客户端：请使用你自己的 Client ID / Secret。

### 1. Google Cloud 控制台

1. 打开 [Google Cloud 控制台 · 凭据](https://console.cloud.google.com/apis/credentials)，新建或选择一个项目。
2. **API 和服务 → 库**：启用 **Gmail API**。
3. **OAuth 同意屏幕**：
   - 用户类型选 **外部**（个人 Gmail 一般如此）。
   - **应用名称**写成可读名称（例如「EdgeSidekick Gmail」）。浏览器登录页会显示这个名字；若写成「1」就会看到「1」尚未完成验证。
   - 发布状态若为 **测试中**：在「测试用户」里 **添加你要授权的那个 Google 邮箱**。未添加会出现 **错误 403：access_denied**。
   - 不必为了本机自用去走 Google 正式「验证」发布（敏感权限验证周期长）；测试用户即可。
4. **凭据 → 创建凭据 → OAuth 客户端 ID**：
   - 应用类型选 **桌面应用**（推荐）。
   - 记下 Client ID 与 Client Secret。
   - 若误选成「Web 应用」，须在「已授权的重定向 URI」中加入本机回调，例如：
     `http://127.0.0.1:8787/api/plugins/oauth/callback`
     （端口以 Sidekick 实际端口为准，默认 8787。）

### 2. 在 Sidekick 里授权

1. 「设置 → 插件 → Gmail」填入 Client ID、Client Secret，点 **保存并验证**（此时还没有 Refresh token 属正常）。
2. 再点 **用 Google 授权**：系统浏览器打开 Google 登录页；用 **已加入测试用户的账号** 同意。
3. 看到授权成功页后回到 Sidekick，点 **验证**。通过后即可在对话里使用。

> 申请的权限是 `gmail.modify`（读取、起草、发送、改标签），不包含永久删除邮件。

也可以不走授权按钮：在 [OAuth Playground](https://developers.google.com/oauthplayground) 用自己的 Client ID/Secret 换取 refresh token 后手动粘贴。

### 常见问题

| 现象 | 处理 |
| --- | --- |
| 浏览器显示应用名「1」、测试中、403 access_denied | 同意屏幕改名；把当前 Google 账号加入测试用户后重试 |
| 保存并验证提示授权无效/已撤销 | 多为未真正完成授权或 Client 与 token 不匹配 → 重新「用 Google 授权」 |
| 授权窗口打开后 Sidekick 无反应 | 桌面端用系统浏览器授权；完成后回到本页点「验证」 |
