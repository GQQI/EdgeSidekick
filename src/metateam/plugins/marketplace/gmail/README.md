# Gmail 插件

让智能体帮你查邮件、读会话、整理要点，并起草或发送回复（发送前一定会弹出确认）。

## 配置（约 3 分钟）

1. 打开 [Google Cloud 控制台](https://console.cloud.google.com/apis/credentials)，新建或选择一个项目。
2. 「API 和服务 → 库」里启用 **Gmail API**。
3. 「OAuth 同意屏幕」选「外部」，把自己的邮箱加入测试用户。
4. 「凭据 → 创建凭据 → OAuth 客户端 ID」，应用类型选 **桌面应用**，得到 Client ID 和 Client Secret。
5. 在「设置 → 插件 → Gmail」填入两者并保存，点「用 Google 授权」，在弹出的页面同意授权即可。授权完成后 Refresh token 会自动保存。

> 申请的权限是 `gmail.modify`（读取、起草、发送、改标签），不包含永久删除邮件。

也可以不走授权按钮：在 [OAuth Playground](https://developers.google.com/oauthplayground) 用自己的 Client ID/Secret 换取 refresh token 后手动粘贴。
