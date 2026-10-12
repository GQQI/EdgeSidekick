# Bundled skills — sources and licenses

These folders are seed skills copied into a new user's skill library.
They are instruction packages (Markdown + optional local scripts). None
of them require network access at runtime.

## Apache License 2.0

From [anthropics/skills](https://github.com/anthropics/skills)
(example skills). Each folder ships its own `LICENSE.txt`.

| Skill | Notes |
|-------|--------|
| `skill-creator` | Official skill authoring guide. EdgeSidekick adapter: `skill_save` → `SKILLS_DIR`; Claude Code eval scripts omitted. |
| `frontend-design` | Distinctive UI direction. |
| `canvas-design` | Static visual / poster direction. Fonts are OFL license texts, not binary font files. |
| `internal-comms` | Internal writing templates (examples included). |
| `theme-factory` | Ten bundled color themes. |
| `discernment-nudge` | Follow-up questions after substantive answers. |
| `doc-coauthoring` | Doc workshop. Claude.ai / connector steps rewritten for offline use. |

## MIT License

From [obra/superpowers](https://github.com/obra/superpowers)
(copyright Jesse Vincent). Each folder ships its own `LICENSE.txt`.
Descriptions and paths were narrowed so they trigger on coding work,
not on EdgeSidekick document/report tasks, and so they do not auto-commit.

| Skill | Notes |
|-------|--------|
| `brainstorming` | Design before code. Specs go to `docs/specs/`. |
| `writing-plans` | Implementation plans to `docs/plans/`. |
| `systematic-debugging` | Root-cause before fixes. |
| `test-driven-development` | Red-green-refactor. |
| `verification-before-completion` | Evidence before "done". |

## Intentionally not bundled

Official skills that need network, extra runtimes, or Anthropic-only
branding were skipped: `claude-api`, `mcp-builder`, `docx` / `pdf` /
`pptx` / `xlsx` (pip/npm), `webapp-testing`, `slack-gif-creator`,
`brand-guidelines`.
