# EdgeSidekick website (GitHub Pages)

Path: `website/`. Deployed by `.github/workflows/pages.yml`.

Live: https://gqqi.github.io/EdgeSidekick/

## Pages

| File | Role |
|---|---|
| `index.html` | Home — hero, architecture diagram, capability rows |
| `features.html` | Features — architecture, capability detail, flow, screenshots |
| `download.html` | Download — Windows Setup from GitHub Releases |
| `docs/user-guide.*.html` | Handbook |
| `promo.html` | Optional promo animation |
| `assets/architecture.svg` | Static architecture diagram |
| `assets/brand-icon.png` | Header brand icon |

Nav links open **separate HTML pages**. Language and theme controls are spaced apart in the header.

## Download URL

```
https://github.com/GQQI/EdgeSidekick/releases/latest/download/EdgeSidekick-Setup-0.3.1-win-x64.exe
```

`js/main.js` also queries the Releases API for the latest Setup / ZIP assets.

Local build output (not in git): `desktop/dist/EdgeSidekick-Setup-<version>-win-x64.exe`

## Preview locally

```powershell
cd path\to\EdgeSidekick\website
python -m http.server 8080
```

- http://127.0.0.1:8080/
- http://127.0.0.1:8080/features.html
- http://127.0.0.1:8080/download.html
