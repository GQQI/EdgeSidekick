# EdgeSidekick website (GitHub Pages)

Path: `website/`. Deployed by `.github/workflows/pages.yml`.

Live: https://gqqi.github.io/EdgeSidekick/

## Single page

Primary UX is **one scrolling `index.html`**:

| Anchor | Section |
|---|---|
| `#top` / hero | Product intro |
| `#highlights` | Three short highlights |
| `#architecture` | Inline core-algorithm SVG (no download link) |
| `#features` | Capabilities, security, getting started |
| `#screenshots` | Workbench previews |
| `#download` | Windows installer + source run |

`features.html` and `download.html` redirect to `#features` / `#download`.

Minimal header: brand icon + language / theme toggles only (no page-nav buttons).

## Download URL

```
https://github.com/GQQI/EdgeSidekick/releases/latest/download/EdgeSidekick-Setup-0.3.1-win-x64.exe
```

`js/main.js` also queries the Releases API for the latest Setup / ZIP assets.

## Preview locally

```powershell
cd path\to\EdgeSidekick\website
python -m http.server 8080
```

- http://127.0.0.1:8080/
- http://127.0.0.1:8080/#architecture
- http://127.0.0.1:8080/#download
