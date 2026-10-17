# Navigation

The sidebar lists every page, can be collapsed on desktop and becomes a drawer below 768 px; the breadcrumb names the current page once after the app name.

## Sub-features

- `nav-desktop` shows the sidebar as a column at 1440 (`data-mode="column"`).
- `nav-drawer` hides the sidebar at 390 and opens it as a drawer with a backdrop from `Open menu`.
- `nav-close` closes the drawer from the backdrop, `Close sidebar`, or choosing a page.
- `nav-crumbs` reads `<app> / <page>` with the page's own label, including Credentials and LLM Providers.

## How to get to it (user POV)

- Any page at desktop width shows the sidebar.
- At phone width, the `Open menu` button in the top bar.
- The breadcrumb in the top bar on every page.

## Driving it with control-embry0

Preconditions:

- `doctor` reports `worth_driving: true`.

- **Desktop.** Run `control-embry0 probe --route / --width 1440`. `sidebar.mode` is `column`, `sidebar.visible` is true and `horizontal_scroll` is false.
- **Phone.** Run `control-embry0 probe --route / --width 390`. `sidebar` is null or not visible, `open_menu_button` is true and `horizontal_scroll` is false.
- **Drawer.** Run `control-embry0 shot --route / --width 390 --path artifacts/nav-390.png`. The screenshot shows the top bar with the menu button and no sidebar over the content.
- **Breadcrumbs.** Run `control-embry0 probe --route /credentials` and `control-embry0 probe --route /providers`. `breadcrumbs` ends with `Credentials` and `LLM Providers`, and the app name appears once.

## Gotchas

- The drawer flag is not persisted; a reload at 390 starts closed. Do not read a closed drawer after a reload as a failure.
- The desktop open or collapsed state and the density mode are persisted in `localStorage` (`embry0-layout`); a fresh browser context starts from the default.
- The breadcrumb's app name is a link; the probe deduplicates text, so read the screenshot when the order matters.
