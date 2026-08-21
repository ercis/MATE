# Landing page

The public marketing page for [PM-MATE](https://pm-mate.uni-muenster.de), served
from GitHub Pages. One dependency-free `index.html` plus `assets/` - no build
step, no framework, no tracking.

Deployed by [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) on
every push to `main` that touches this directory. Edit `index.html`, push, done.

To preview locally:

```bash
python3 -m http.server -d landing 8081
```

## Screenshots

`index.html` expects six PNGs in `assets/`. Any that are missing fall back to
`assets/placeholder.svg` ("Screenshot pending"), so the page never shows a
broken image - drop the real file in and it takes over on the next deploy.

| File | Where it appears | What to capture |
| --- | --- | --- |
| `shot-hero.png` | Hero, full width | A process overview with the module library visible and the MATE AI sidebar open - the one shot that has to sell the product at a glance. |
| `shot-processes.png` | Tab 01, Workspace | The processes list with several imported logs, at least one of them object-centric (OCEL), so the format badges show. |
| `shot-modules.png` | Tab 02, Module library | One process's module library, scrolled so the category grouping (discovery / intelligence / comparison) is readable. |
| `shot-crossmodule.png` | Tab 03, Cross-module | Performance over Time on a log with a CV4CDD-detected drift: the shaded drift band must be visible in the chart, so one module's result shows up inside another's view. |
| `shot-dashboard.png` | Tab 04, Dashboards | A dashboard with a mix of widget kinds on one canvas - KPI tiles, a chart, and a model view. |
| `shot-mate-ai.png` | Tab 05, MATE AI | The MATE AI sidebar mid-conversation next to a process page, with an answer that shows it reasoning about the process. |

Capture notes:

- **Size**: a 1440x1020 browser viewport at 2x (so 2880x2040 PNG). The theater
  frame crops to a 24:17 aspect and anchors to the top, so a little extra height
  is fine but extra width gets cut.
- **Chrome**: content only, no browser UI, no OS window shadow. Light theme.
- **Data**: this page is public. Use demo logs - no real client data, no real
  user names, and check the account menu and any "shared with" avatars before
  shipping a shot.
- **Weight**: run them through an optimizer (`oxipng -o4`, `pngquant`) and aim
  for well under 1 MB each; the hero is the first thing that loads.
