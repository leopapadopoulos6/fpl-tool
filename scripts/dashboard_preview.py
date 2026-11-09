"""Render the dashboard page locally, outside claude.ai, for layout and behaviour checks.

The real page runs inside the claude.ai Dashboard type, which mounts it in ``#dash-root`` and
gives its scripts ``d3`` and ``dash``. This harness imitates that closely enough to test the
page at phone and desktop widths:
  - theme tokens (light, or dark with ``?theme=dark``) named as the type guarantees them;
  - a stub ``dash`` with data / draw / onData / params / setParams / setLink / colors, fed from
    a directory of dataset JSON files (``fpl dashboard-data --out DIR``);
  - each page ``<script>`` runs in its own scope after the page is mounted, as in the type;
  - drawings inside ``hidden`` never run, and run again when the attribute comes off.
``window.__dash`` exposes the call log (setParams, setLink) and any thrown errors.

    uv run python scripts/dashboard_preview.py --data DIR --d3 path/to/d3.min.js --out page.html
    [--without decision,lineup,...]   # simulate datasets removed by the gate
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "src" / "fpl" / "ui" / "dashboard_page.html"

TOKENS = """
:root { --font-anthropic-sans: system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-anthropic-serif: Georgia, "Times New Roman", serif;
  --cds-font-size-caption: 12px; --cds-font-size-body: 14px; --cds-font-size-heading: 16px;
  --cds-font-size-title: 26px; --cds-font-weight-medium: 560;
  --cds-gap-xs: 4px; --cds-gap-sm: 8px; --cds-gap-md: 12px; --cds-gap-lg: 16px; --cds-gap-xl: 24px;
  --cds-pad-xs: 4px; --cds-pad-sm: 8px; --cds-pad-md: 12px; --cds-pad-lg: 16px; --cds-pad-xl: 24px;
  --cds-radius: 8px; --cds-dur-slow: 240ms; --cds-ease-out: cubic-bezier(.2,.8,.2,1);
  --color-fg: #1f1e1d; --color-fg-muted: #6b6a66; --color-bg: #faf9f5; --color-panel: #ffffff;
  --color-border-line: #e3e0d8; --color-ok: #2f7d32; --color-warn: #a86400; --color-bad: #c3362b;
  --cds-surface-popover: #fff; --cds-border: #d9d6ce; --cds-shadow-popover: 0 4px 16px rgb(0 0 0 / .12);
  --cds-text-secondary: #6b6a66; --cds-chart-muted: #d8d5cd; --cds-chart-grid: #ece9e2;
  --cds-chart-axis: #c9c6be; --cds-chart-reference: #9a978f; --chart-1: #2c6fbb; --chart-2: #c26a2b; }
:root[data-theme=dark] { --color-fg: #ecebe6; --color-fg-muted: #a3a29c; --color-bg: #1e1d1b;
  --color-panel: #272623; --color-border-line: #3a3935; --color-ok: #6fbf73; --color-warn: #e0a040;
  --color-bad: #ee6b5f; --cds-surface-popover: #2f2e2a; --cds-border: #45443f;
  --cds-text-secondary: #a3a29c; --cds-chart-muted: #3d3c38; --cds-chart-grid: #33322e;
  --cds-chart-axis: #4a4944; --cds-chart-reference: #77766f; --chart-1: #6ea8e8; }
html, body { margin: 0; }
body { background: var(--color-bg); color: var(--color-fg); font-family: var(--font-anthropic-sans);
  font-size: var(--cds-font-size-body); line-height: 1.45; }
#dash-root { padding: 16px; }
.dash-select { font: inherit; color: var(--color-fg); background-color: var(--color-panel);
  border: 1px solid var(--color-border-line); border-radius: var(--cds-radius); padding: 6px 28px 6px 10px; }
"""

STUB = r"""
(() => {
  const DATA = __DATA__;
  const params = __PARAMS__;
  const draws = new Map(), onDatas = [], log = [], errors = [];
  const report = (e) => { errors.push(String(e && e.stack || e)); console.error(e); };
  const run = (name) => {
    const fn = draws.get(name);
    if (!fn) return;
    document.querySelectorAll('[data-draw="' + name + '"]').forEach((el) => {
      if (el.closest('[hidden]')) return;
      try { fn(el); } catch (e) { report(e); }
    });
  };
  const tick = () => {
    onDatas.forEach((f) => { try { f(); } catch (e) { report(e); } });
    draws.forEach((_, n) => run(n));
  };
  window.dash = {
    data(id) {
      if (!(id in DATA)) return { status: 'missing', data: [], message: '', refreshing: false };
      const rows = DATA[id];
      const d = { status: 'ok', data: rows, message: '', refreshing: false };
      d.columns = rows.length ? Object.keys(rows[0]) : [];
      return d;
    },
    draw(name, fn) { if (fn) draws.set(name, typeof fn === 'function' ? fn : fn.update || fn.mount); else run(name); },
    onData(fn) { onDatas.push(fn); },
    colors: ['var(--chart-1)', 'var(--chart-2)'],
    params() { return { ...params }; },
    setParams(o) { Object.assign(params, o); log.push(['setParams', o]); setTimeout(tick); },
    resetParams() {},
    setLink(n) { log.push(['setLink', n]); },
    refresh() {}, calc() {}, loader() {},
  };
  const setData = (id, rows) => { if (rows == null) delete DATA[id]; else DATA[id] = rows; tick(); };
  window.__dash = { log, errors, tick, params, setData, data: DATA };
  let w = innerWidth;
  addEventListener('resize', () => { if (innerWidth !== w) { w = innerWidth; tick(); } });
  new MutationObserver((ms) => {
    if (ms.some((m) => m.attributeName === 'hidden' && !m.target.hidden)) tick();
  }).observe(document, { subtree: true, attributes: true, attributeFilter: ['hidden'] });
  window.__dashStart = tick;
})();
"""


def render(data_dir: Path, d3: Path, without: set[str], params: dict) -> str:
    data = {p.stem: json.loads(p.read_text()) for p in sorted(data_dir.glob("*.json"))
            if p.stem != "manifest" and not p.stem.startswith("ledger_") and p.stem not in without}
    page = PAGE.read_text()
    scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.S)
    body = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    runner = "\n".join(f"try {{ (function () {{\n{s}\n}})(); }} catch (e) {{ __dash.errors.push(String(e.stack || e)); }}"
                       for s in scripts)
    stub = STUB.replace("__DATA__", json.dumps(data)).replace("__PARAMS__", json.dumps(params))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard preview</title>
<script>if (new URLSearchParams(location.search).get('theme') === 'dark') document.documentElement.dataset.theme = 'dark';</script>
<style>{TOKENS}</style>
<script>{d3.read_text()}</script>
<script>{stub}</script>
</head><body><div id="dash-root">{body}</div>
<script>{runner}
window.__dashStart();</script>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--d3", type=Path, required=True, help="d3 v7 UMD build (d3.min.js)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--without", default="", help="comma-separated dataset ids to leave out")
    ap.add_argument("--params", default='{"squad_view": "formation"}')
    a = ap.parse_args()
    without = {x for x in a.without.split(",") if x}
    a.out.write_text(render(a.data, a.d3, without, json.loads(a.params)))
    print(a.out)


if __name__ == "__main__":
    main()
