#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Render the combined report index and its suite tabs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from common.report import ResultClass

# Tab order is fixed across runs so muscle memory works when a reader
# bookmarks the URL. The same suites appear in :data:`bench.__main__._SEED_ORDER`, in
# the README, and in ``python -m bench --help``; this module owns the
# canonical list for the index page so a future suite lands in both places
# at once.
SHOW_SUITES: tuple[str, ...] = (
    "throughput",
    "server_limits",
    "server_capacity",
    "subscription",
)

# What the per-tab button says when its suite is incomplete. ``complete``
# is the silent default; ``partial`` reads "incomplete" because the page
# still rendered but the data is short of a clean run; ``none`` reads
# "missing" because the per-suite document does not exist (or holds
# nothing to render).
_BADGE_LABELS: dict[ResultClass, str] = {
    "complete": "",
    "partial": "incomplete",
    "none": "missing",
}


@dataclass
class SuiteTab:
    """Inputs the renderer needs to build one tab.

    A renderer does not classify — that is :func:`common.report.classify_results`'s
    job. It does not invoke the per-suite ``show.py`` — that is the CLI's
    job. So every field the renderer needs is on this dataclass,
    pre-classified and pre-resolved.

    ``report_filename`` is the filename the CLI stores the per-suite
    report under (in the database, or already stored there from a
    previous run and being reused) — the name it will be materialized
    under next to ``index.html`` when the page is served. It is the
    ``src`` of this tab's ``<iframe>`` when the suite's state is
    ``complete``. For ``partial`` and ``none`` the iframe is omitted and
    an empty-state card is shown instead.

    ``reason`` / ``recovery_cli`` / ``document_count`` describe the
    empty-state card the renderer emits for any tab whose state is
    ``partial`` or ``none``.

    """

    suite: str
    state: ResultClass
    report_filename: str | None = None
    reason: str = ""
    recovery_cli: str = ""
    document_count: str | None = field(default=None)


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _badge_text(state: ResultClass) -> str:
    """One-line label the tab button shows next to the suite name."""
    label = _BADGE_LABELS[state]
    return f'<span class="badge badge-{state}">{_escape(label)}</span>' if label else ""


def _tab_button(suite: str, state: ResultClass) -> str:
    """The ``<button>`` for one tab, with its badge in the right place."""
    badge = _badge_text(state)
    return (
        f'<button type="button" id="tab-btn-{_escape(suite)}" class="tab-btn" '
        f'data-suite="{_escape(suite)}" '
        f'role="tab" aria-controls="tab-{_escape(suite)}">'
        f"{_escape(suite)} {badge}</button>"
    )


def _empty_card(tab: SuiteTab) -> str:
    """The card shown inside a ``partial``/``none`` tab instead of the iframe."""
    reason = _escape(tab.reason) if tab.reason else ""
    cli = _escape(tab.recovery_cli) if tab.recovery_cli else ""
    count_line = f'<p class="empty-count">{_escape(tab.document_count)}</p>' if tab.document_count else ""
    return (
        f'<div class="empty-card">\n'
        f'  <h2 class="empty-title">{_escape(tab.suite)}</h2>\n'
        f'  <p class="empty-reason">{reason}</p>\n'
        f"  {count_line}\n"
        f'  <p class="empty-cli">Run <code>{cli}</code> to recover.</p>\n'
        f"</div>"
    )


def _tab_panel(tab: SuiteTab, *, visible: bool) -> str:
    """One ``<section>`` holding the per-tab content.

    ``complete`` tabs inline a same-origin ``<iframe src="<report>.html">``
    that loads the per-suite page on demand; ``partial`` and ``none`` tabs
    inline an empty-state card. The ``hidden`` attribute follows the rule
    "first complete tab visible, every other tab hidden on load", so the
    small inline script only has to remove it on click.
    """
    hidden = "" if visible else " hidden"
    if tab.state != "complete" or not tab.report_filename:
        body = _empty_card(tab)
    else:
        # ``loading="lazy"`` so the off-screen iframes do not start
        # downloading their Plotly bundles until the user actually opens
        # the tab. Same-origin, so the browser does not block it under
        # ``file://`` once the user accepts the prompt some browsers
        # show when loading iframes from ``file://``.
        body = (
            f'<iframe class="tab-iframe" '
            f'title="{_escape(tab.suite)} report" '
            f'src="{_escape(tab.report_filename)}" '
            f'loading="lazy"></iframe>'
        )
    return (
        f'<section id="tab-{_escape(tab.suite)}" class="tab-panel" role="tabpanel" '
        f'aria-labelledby="tab-btn-{_escape(tab.suite)}"{hidden}>'
        f"{body}"
        f"</section>"
    )


# All CSS for the index lives in one block. The palette reuses the
# variables the per-suite pages already define (``--surface``, ``--plane``,
# ``--primary``, ``--secondary``, ``--muted``, ``--border``) so a reader
# moving between a per-suite page and the index sees the same surface.
INDEX_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--plane);
  color: var(--primary);
  -webkit-font-smoothing: antialiased;
}
.index-page {
  color-scheme: light;
  --surface: #fcfcfb; --plane: #f9f9f7; --primary: #0b0b0b; --secondary: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --accent: #2a78d6; --ghost: rgba(11,11,11,0.05);
  --warn: #b35900; --warn-dark: #e0b070;
  background: var(--plane); color: var(--primary);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .index-page {
    color-scheme: dark;
    --surface: #1a1a19; --plane: #0d0d0d; --primary: #ffffff; --secondary: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --accent: #3987e5; --ghost: rgba(255,255,255,0.07);
    --warn: #e0b070; --warn-dark: #e0b070;
  }
}
:root[data-theme="dark"] .index-page {
  color-scheme: dark;
  --surface: #1a1a19; --plane: #0d0d0d; --primary: #ffffff; --secondary: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --accent: #3987e5; --ghost: rgba(255,255,255,0.07);
  --warn: #e0b070; --warn-dark: #e0b070;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 28px 20px 72px; }
header.page { display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap; margin-bottom: 22px; }
header.page h1 { font-size: 22px; font-weight: 600; margin: 0 0 6px; letter-spacing: -0.01em; }
header.page p { margin: 0; color: var(--secondary); font-size: 13px; line-height: 1.55; max-width: 72ch; }

/* ---- tab strip --------------------------------------------------------- */
.tab-strip {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin: 0 0 12px; padding: 6px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
}
.tab-btn {
  border: 1px solid transparent; background: transparent;
  padding: 7px 14px; font-size: 13px; line-height: 1.4;
  color: var(--secondary); cursor: pointer; border-radius: 7px;
  font: inherit; font-weight: 500;
}
.tab-btn:hover { background: var(--ghost); }
.tab-btn[aria-pressed="true"] {
  background: var(--accent); color: #fff; border-color: transparent;
}
.tab-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.badge {
  display: inline-block; margin-left: 7px; padding: 1px 7px;
  font-size: 11px; font-weight: 500; line-height: 1.5;
  border-radius: 999px; vertical-align: 1px;
}
.badge-partial { background: rgba(179, 89, 0, 0.12); color: var(--warn); }
.badge-none { background: rgba(208, 59, 59, 0.12); color: #b03030; }
:root[data-theme="dark"] .badge-none { color: #ff8585; }

/* ---- tab panels -------------------------------------------------------- */
.tab-panel { display: block; }
.tab-panel[hidden] { display: none; }
.tab-iframe {
  display: block; width: 100%; height: calc(100vh - 220px); min-height: 600px;
  border: 1px solid var(--border); border-radius: 10px;
  background: var(--surface);
}

/* ---- empty-state card -------------------------------------------------- */
.empty-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 28px 28px 22px; max-width: 720px;
}
.empty-card .empty-title {
  font-size: 17px; font-weight: 600; margin: 0 0 6px;
  color: var(--primary); letter-spacing: -0.01em;
}
.empty-card .empty-reason { margin: 0 0 16px; color: var(--secondary); font-size: 13.5px; }
.empty-card .empty-count {
  display: inline-block; margin: 0 0 16px;
  padding: 4px 10px; border-radius: 6px;
  background: var(--ghost); color: var(--secondary);
  font-variant-numeric: tabular-nums; font-size: 12.5px;
}
.empty-card .empty-cli {
  margin: 16px 0 0; padding-top: 14px;
  border-top: 1px solid var(--border);
  color: var(--secondary); font-size: 13px;
}
.empty-card .empty-cli code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px; padding: 1px 6px;
  background: var(--plane); border: 1px solid var(--border); border-radius: 4px;
}

footer.page { color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 34px; }
footer.page code { font-size: 11.5px; }
.hidden { display: none !important; }
"""


# A short, dependency-free tab switcher. Event delegation on the strip is
# one listener, not one per button, so it survives a re-render of the
# buttons. ``hidden`` is the HTML attribute that says "this element is not
# yet relevant", and removing it (rather than toggling ``display``) leaves
# the per-suite pages' own CSS untouched.
INDEX_JS = r"""
'use strict';
(function () {
  const strip = document.querySelector('.tab-strip');
  if (!strip) return;
  strip.addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-suite]');
    if (!btn) return;
    const suite = btn.getAttribute('data-suite');
    strip.querySelectorAll('button[data-suite]').forEach((b) => {
      b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
    });
    document.querySelectorAll('.tab-panel').forEach((panel) => {
      if (panel.id === 'tab-' + suite) {
        panel.removeAttribute('hidden');
      } else {
        panel.setAttribute('hidden', '');
      }
    });
  });
  // Mark the active button for screen readers and CSS state. The default
  // active button is the one whose panel is not hidden on load.
  const initialPanel = document.querySelector('.tab-panel:not([hidden])');
  if (initialPanel) {
    const suite = initialPanel.id.replace(/^tab-/, '');
    const activeBtn = strip.querySelector('button[data-suite="' + suite + '"]');
    if (activeBtn) activeBtn.setAttribute('aria-pressed', 'true');
  }
})();
"""


def _ordered_tabs(tabs: Iterable[SuiteTab]) -> list[SuiteTab]:
    """Return ``tabs`` in the canonical order, with unknown suites dropped."""
    by_name = {tab.suite: tab for tab in tabs}
    return [by_name[suite] for suite in SHOW_SUITES if suite in by_name]


def _first_complete(tabs: list[SuiteTab]) -> str | None:
    """The name of the first ``complete`` tab, or ``None`` if every tab is missing/partial."""
    for tab in tabs:
        if tab.state == "complete":
            return tab.suite
    return None


def render_index_page(folder_name: str, tabs: Iterable[SuiteTab]) -> str:
    """Build the tabbed ``index.html`` for ``folder_name`` from pre-classified ``tabs``."""
    ordered = _ordered_tabs(tabs)
    active = _first_complete(ordered)

    tab_buttons = "\n".join(_tab_button(tab.suite, tab.state) for tab in ordered)
    tab_panels = "\n".join(_tab_panel(tab, visible=(tab.suite == active)) for tab in ordered)

    counts = {state: sum(1 for tab in ordered if tab.state == state) for state in ("complete", "partial", "none")}
    summary = (
        f"{counts['complete']} complete, {counts['partial']} partial, {counts['none']} none"
        if counts["partial"] or counts["none"]
        else f"{counts['complete']} complete"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Benchmark index — {_escape(folder_name)}</title>
<style>{INDEX_CSS}</style>
</head>
<body>
<div class="index-page">
<div class="wrap">
<header class="page">
  <div>
    <h1>Benchmark index — {_escape(folder_name)}</h1>
    <p>One tab per suite, in fixed order.
    Report availability: {summary}. A renderable report may contain failed or incomplete measurements;
    its measurement status is shown inside the tab.</p>
  </div>
</header>

<div class="tab-strip" role="tablist">
{tab_buttons}
</div>

{tab_panels}

<footer class="page">
  <p>Generated by <code>python -m bench.show {_escape(folder_name)}</code>.
  Each tab loads its suite's <code>&lt;suite&gt;.html</code> on demand.</p>
</footer>
</div>
</div>
<script>{INDEX_JS}</script>
</body>
</html>
"""
