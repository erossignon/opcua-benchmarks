# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.
"""The look of the throughput report, for the other reports to share.

Colour tokens (light, dark, and a toggle that overrides the system scheme), the page
header, cards and tables of ``throughput/show.py``. A page wraps its content in
``<div class="viz-root"><div class="wrap">`` and puts ``THEME_TOGGLE`` in its header.
"""

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'

_LIGHT = """--surface: #fcfcfb; --plane: #f9f9f7; --primary: #0b0b0b; --secondary: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --accent: #2a78d6; --ghost: rgba(11,11,11,0.05); --good: #0ca30c; --warn: #b7791f; --critical: #d03b3b;"""
_DARK = """--surface: #1a1a19; --plane: #0d0d0d; --primary: #ffffff; --secondary: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --accent: #3987e5; --ghost: rgba(255,255,255,0.07); --good: #0ca30c; --warn: #d4a72c; --critical: #d03b3b;"""

BASE_CSS = f"""
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: {FONT_STACK}; -webkit-font-smoothing: antialiased; }}
.viz-root {{ min-height: 100vh; background: var(--plane); color: var(--primary); color-scheme: light;
  {_LIGHT} }}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .viz-root {{ color-scheme: dark; {_DARK} }}
}}
:root[data-theme="dark"] .viz-root {{ color-scheme: dark; {_DARK} }}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 72px; }}
header.page {{ display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap; margin-bottom: 22px; }}
header.page h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 6px; letter-spacing: -0.01em; }}
header.page p {{ margin: 0; color: var(--secondary); font-size: 13px; line-height: 1.55; max-width: 68ch; }}
.spacer {{ flex: 1 1 auto; }}
.presets {{ display: flex; gap: 6px; margin-left: auto; }}
.presets button {{ font: inherit; border: 1px solid var(--border); background: transparent; border-radius: 7px;
  padding: 4px 9px; font-size: 12px; color: var(--secondary); cursor: pointer; }}
.presets button:hover {{ background: var(--ghost); }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 18px 14px; margin-bottom: 22px; }}
.card > header {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }}
.card h2 {{ font-size: 15px; font-weight: 600; margin: 0; }}
.card .q {{ font-size: 12.5px; color: var(--muted); }}
.card .caption {{ font-size: 13px; color: var(--secondary); line-height: 1.55; margin: 12px 2px 0;
  border-top: 1px solid var(--border); padding-top: 11px; }}
.card .caption + .caption {{ border-top: 0; padding-top: 0; }}
.tablewrap {{ overflow-x: auto; margin-top: 4px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; }}
th, td {{ text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
  position: sticky; top: 0; background: var(--surface); }}
td {{ font-variant-numeric: tabular-nums; color: var(--secondary); }}
td.key {{ color: var(--primary); }}
td.left, th.left {{ text-align: left; }}
.badge {{ font-size: 11px; border-radius: 4px; padding: 1px 6px; border: 1px solid currentColor; }}
.badge.good {{ color: var(--good); }} .badge.warn {{ color: var(--warn); }} .badge.bad {{ color: var(--critical); }}
.meter {{ display: block; height: 8px; border-radius: 3px; background: var(--accent); min-width: 2px; }}
details {{ margin: 10px 2px; }}
details > summary {{ cursor: pointer; font-size: 13px; font-weight: 600; color: var(--secondary); }}
details .tablewrap {{ max-height: 70vh; overflow: auto; margin-top: 8px; }}
pre {{ white-space: pre-wrap; font-size: 12px; color: var(--muted); }}
footer.page {{ color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 34px; }}
"""

# The light/dark override of throughput's report: a button with data-themetoggle in the header.
THEME_TOGGLE = '<div class="presets"><button type="button" data-themetoggle>Dark theme</button></div>'
THEME_TOGGLE_JS = """
(() => {
  const dark = () => (document.documentElement.getAttribute('data-theme')
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark';
  const button = document.querySelector('[data-themetoggle]');
  const label = () => { if (button) button.textContent = dark() ? 'Light theme' : 'Dark theme'; };
  if (button) button.addEventListener('click', () => {
    document.documentElement.setAttribute('data-theme', dark() ? 'light' : 'dark');
    label();
  });
  label();
})();
"""
