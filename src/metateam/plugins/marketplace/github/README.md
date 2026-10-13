# GitHub 插件

让智能体直接读写 GitHub：搜索仓库和 Issue/PR、阅读 PR 改动做代码评审、读取仓库文件和提交记录，并在你确认后创建 Issue 或发表评论。

## 配置

1. 打开 <https://github.com/settings/personal-access-tokens/new> 创建 Fine-grained token。
2. 只勾选需要访问的仓库，并授予权限：
   - 只读：Contents、Issues、Pull requests、Metadata
   - 需要创建 Issue/评论时：Issues、Pull requests 的 Read and write
3. 在「设置 → 插件」里粘贴 token，点「验证」。

GitHub Enterprise 用户把「API 地址」改成 `https://你的域名/api/v3`。

## 工具

| 工具 | 说明 | 是否写操作 |
| --- | --- | --- |
| 搜索仓库 | GitHub 搜索语法 | 否 |
| 搜索 Issue / PR | 如 `repo:owner/name is:open label:bug` | 否 |
| 读取 Issue | 正文 + 最近评论 | 否 |
| 列出 PR / 读取 PR 改动 | 含每个文件的 diff 片段 | 否 |
| 读取文件 / 提交记录 | 指定分支、标签或提交 | 否 |
| 创建 Issue / 发表评论 | 执行前会弹出确认 | 是 |
