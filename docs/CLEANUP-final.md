# CLEANUP-final (2026-10-12, Asia/Shanghai)

Single consolidated cleanup log. Prior dated CLEANUP-* notes were merged here and removed.

## This pass (thorough residual cleanup)

Stopped two local `main.py serve` processes that locked `.venv` and `_server_*.txt`, then deleted:

### Directories removed
- `.venv/` (~192 MB, machine-specific Python env)
- `tests/` (entire Python test suite; user-requested)
- `ui/node_modules/` (~196 MB; rebuild with `npm install` in `ui/`)
- `ui/dist/` (Vite build artifact)
- `desktop/build/` (Electron build artifact)
- `.pytest_cache/`
- All `src/metateam/**/__pycache__/` (9 dirs)

### Files removed
- `_server_err.txt`, `_server_out.txt` (empty server capture logs; were file-locked by serve)
- `docs/CLEANUP-2026-10-12.md`
- `docs/CLEANUP-2026-10-12-shipping.md`
- `docs/CLEANUP-2026-10-12-readme-resync.md`
- Transient `docs/_deleted_batch1.txt`, `docs/_failed_batch1.txt`

### Not present / skipped
- `desktop/node_modules/` — already absent
- `packaging/**/payload` — **not present** on disk this pass (no ~921 MB payload found under packaging); if reintroduced, keep gitignored / do not commit

## Prior passes (summary of deleted items from earlier CLEANUP notes)

### Root scratch / diag (earlier)
- `_diag2.txt` `_diag3.txt` `_diag4.txt` `_diag_out.txt`
- Assorted `_fix_*.py` / `_split_*.py` / `_view_scan.py` / `_wire_syms.py` / temp scrub helpers
- `_outlines.txt` `_outlines_b64.txt` and similar capture dumps

### Shipping / secrets scrub (earlier)
- `src/data/.local_token`, `.secret_key`
- Emptied `auth_tokens.json` / `users.json`; redacted tenant `api_key` fields
- Cleared personal tenants (kept `default`), sessions (kept `.gitkeep`), `fs_undo`, `book_to_skill_jobs`
- Deleted `docs/screenshots/_capture*` logs and personal-chat social gallery shots used in marketing
- Deleted `scripts/verify_group_chat/results*.json`

### Branding / assets (earlier)
- Scrubbed military-styled UI wording; promo `.hud` → `.promo-bar`
- Removed obsolete screenshots (`color-check-*`, `logo-style-*`, `live-workbench-*`, etc.)
- README rewritten (EN primary + `README_CN.md`); EdgeSidekick identity strings

## Intentionally preserved

- Source: `src/`, `main.py`, `ui/src/`, `desktop/*.mjs|cjs`, `website/`, `landing.html`
- Docs keepers: `README.md`, `README_CN.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`
- Feature docs: `docs/book-to-skill.md`, `docs/browser-sandbox.md`
- Packaging READMEs: `packaging/windows/README.md`, `packaging/kylin/README.md`
- Website docs: `website/docs/user-guide.*.html`, `website/README.md`, `desktop/README.md`
- Package texts: `requirements.txt` (no `requirements.lock.txt` present)
- Skills / licenses under `src/skills/` and `src/data/tenants/default/skills/` (product payload)
- `data/demo-ws/README.txt`
- Build scripts under `scripts/` (including capture helpers; not deleted this pass)
- `pytest.ini` left (harmless; tests dir gone — recreate tests later if needed)

## Rebuild notes

```text
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd ui && npm install && npm run build
cd desktop && npm install   # if/when packaging desktop
```

## Remaining top-level tree size (approx after this pass)

| Dir        | ~MB |
|------------|-----|
| src        | 12.7 |
| ui         | 3.6 |
| website    | 0.8 |
| docs       | 0.7 |
| desktop    | 0.2 |
| data       | 0.1 |
| scripts    | 0.1 |
| packaging  | 0 |

No git push performed.
