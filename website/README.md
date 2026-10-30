# EdgeSidekick website (GitHub Pages)

Path: `website/` (repo root). Deployed by `.github/workflows/pages.yml` from this folder.

Live: https://gqqi.github.io/EdgeSidekick/

## Pages

| File | Role |
|---|---|
| `index.html` | Home |
| `features.html` | Features / screenshots / demo link |
| `download.html` | Download (Windows Setup from GitHub Releases) |
| `docs/user-guide.*.html` | User handbook |
| `promo.html` | Optional promo animation |

Nav links open **separate HTML pages** (not in-page anchors).

## Download URL

Primary Windows installer:

```
https://github.com/GQQI/EdgeSidekick/releases/latest/download/EdgeSidekick-Setup-0.3.1-win-x64.exe
```

`js/main.js` also queries the GitHub Releases API and rewires `#dlSetup` / `#dlZip` to the latest matching assets when a release exists.

**Local build output (not in git):** `desktop/dist/EdgeSidekick-Setup-<version>-win-x64.exe`  
See `packaging/windows/README.md`. Publish that file as a GitHub Release asset so the download button resolves.

There were **no public Releases** at the time this page was wired; the button still points at the Releases download URL above.

## Preview locally

```powershell
cd path\to\EdgeSidekick\website
python -m http.server 8080
```

- Home: http://127.0.0.1:8080/
- Features: http://127.0.0.1:8080/features.html
- Download: http://127.0.0.1:8080/download.html
- Guide: http://127.0.0.1:8080/docs/user-guide.zh.html

## i18n / theme

- Default language: Chinese (`zh`); toggle EN / 中文 (`localStorage`: `esk-lang`)
- Theme: light / dark (`esk-theme`)
