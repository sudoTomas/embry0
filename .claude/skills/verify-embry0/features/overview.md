# Overview

The first screen sums up the deployment: stat tiles, the most recent issues with their state, and the watcher's last runs.

## Sub-features

- `ov-tiles` shows the stat tiles from `/api/v1/stats`.
- `ov-issues` lists recent issues with links to each.
- `ov-watcher` shows the watcher panel with its last runs.

## How to get to it (user POV)

- The `Overview` entry at the top of the sidebar.
- The app name in the breadcrumb.
- The root URL.

## Driving it with control-embry0

Preconditions:

- `doctor` reports `worth_driving: true`.

- **Render.** Run `control-embry0 probe --route /`. `errors` is empty and `text_chars` is above 200.
- **Tiles match the API.** Run `control-embry0 api /api/v1/stats` and `control-embry0 read --route / --selector main`. The numbers in the tiles appear in the API body.
- **Phone.** Run `control-embry0 shot --route / --width 390 --path artifacts/overview-390.png`. Tiles stack in one column and `horizontal_scroll` is false.

## Gotchas

- The tab title carries a count prefix when items need a person; compare titles without it.
- Tiles poll; a job that finishes during the run can change a number between `api` and `read`.
