# Book → Skill（完全离线）

把用户上传的 **PDF / EPUB** 蒸馏成 **用户个人配置 / 租户技能库** 中的 Skill 包，写入
`SIDEKICK_DATA_DIR/data/tenants/<user>/skills`（桌面端通常为 `%APPDATA%\EdgeSidekick\...`），
供现有 `/skills`、`/skill <name>` 与 Skills 面板发现。

**不会**写入仓库内的 `src/skills`（捆绑技能树）。

## 设计要点

- **个人/租户安装**：默认写入当前登录用户的 tenant skills 目录（与
  `apply_knowledge_to_settings` 绑定的 `settings.skills_dir` 一致），不是项目源码树。
- **自研异步 Job**：`pending → extracting → distilling → validating → installed | failed`，进程内线程，不调用外部 book-to-skill CLI / Claude Code，运行时不下载远程包。
- **自有模型**：蒸馏走 `metateam.runtime.llm.LLM.complete_text`（与聊天/Agent 同一套模型配置）。
- **离线提取**：PDF 用 `pypdf`；EPUB 用标准库 zip + HTML 解析。不需要外网即可 extract；LLM 命中你在设置里配置的本地/内网端点即可全离线蒸馏。
- **按章切片**：大书按标题或字数切片；单章有字符上限；**从不**把整本书塞进一次 LLM 调用。SKILL.md 保持精简，章节进 `references/chapters/`，Agent 按需 `read_file`。

## 安装路径

| 场景 | 路径 |
|------|------|
| 已设置 `SIDEKICK_DATA_DIR`（桌面打包） | `$SIDEKICK_DATA_DIR/data/tenants/<user_id>/skills/<slug>/` |
| 开发态未设置 env（`ROOT == SRC_ROOT`） | 主存 `%APPDATA%/EdgeSidekick/data/tenants/<user_id>/skills/`（与 Electron userData 一致），并镜像到运行时 tenant 目录供 Skills UI / `/skills` 列举；**禁止**写入 `src/skills`。 |

## API

Base: `http://127.0.0.1:8787`（需本地 token，与其它 API 相同）。

### 创建任务

`POST /api/book-to-skill` multipart:

| 字段 | 说明 |
|------|------|
| `file` | PDF 或 EPUB |
| `name` | 可选，目标 skill slug |
| `overwrite` | `true`/`false`，默认 false |

返回：`{ "status": "ok", "job": { job_id, status, progress, ... } }`

### 查询

- `GET /api/book-to-skill` — 任务列表
- `GET /api/book-to-skill/{job_id}` — 进度/状态
- `POST /api/book-to-skill/{job_id}/cancel` — 请求取消（协作式）

### curl 示例

```bash
curl -s -X POST "http://127.0.0.1:8787/api/book-to-skill" \
  -H "X-Sidekick-Token: $TOKEN" \
  -F "file=@./mybook.pdf" \
  -F "name=mybook-skill" \
  -F "overwrite=false"
```

轮询：

```bash
curl -s "http://127.0.0.1:8787/api/book-to-skill/<job_id>" \
  -H "X-Sidekick-Token: $TOKEN"
```

`status=installed` 后：

```text
/skills
/skill mybook-skill
```

## 生成包结构

```text
<data/tenants/<user>/skills>/<slug>/
  SKILL.md
  references/
    SOURCE.txt          # 源文件名与 user-provided 声明
    glossary.md
    patterns.md
    cheatsheet.md
    chapters/
      01-....md
      ...
```

## UI

设置 → Skills：可用「书本→技能」上传 PDF/EPUB 并查看任务进度。安装到当前用户的个人/租户技能库。

## 限制 / 缺口

- 扫描版 PDF（纯图片）无 OCR，会失败并提示。
- EPUB 为 best-effort（无 ebooklib）；复杂排版可能丢结构。
- 蒸馏质量取决于当前配置的模型；demo 无 key 时会退化为启发式摘要。
- 中文文件名若无法 slug 化，会回退为 `book-<hash>`（可用 `name` 参数指定）。
