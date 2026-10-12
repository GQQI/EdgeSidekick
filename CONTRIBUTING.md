# Contributing to EdgeSidekick

Thanks for your interest in improving EdgeSidekick.

## Ways to help

- **Bug reports** — open an issue with steps to reproduce, OS, and app version.
- **Features** — describe the use case and how it fits local-first workflows.
- **Docs / website** — improve website/, handbooks under website/docs/, or README translations.
- **Code** — keep changes focused; match existing Python / TypeScript style.

## Local development

1. Python 3.10+ and Node.js 18+
2. pip install -r requirements.txt
3. python main.py (API at http://127.0.0.1:8787)
4. Optional UI: cd ui && npm i && npm run dev
5. Desktop: start-desktop.bat (Windows) or see desktop/README.md

Never commit secrets (.env, API keys, src/data/model.json, sessions).

## Pull requests

- One concern per PR when practical
- Include a short test plan
- Update docs if behavior changes

Community channels (Discussions / Discord / X) are placeholders until published — see the README.
