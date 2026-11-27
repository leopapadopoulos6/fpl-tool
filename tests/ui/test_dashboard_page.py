"""Browser checks of the dashboard page (section nav, squad formation/list, engine diff,
player detail) at phone, side-pane and desktop widths, light and dark.

Needs Node with ``playwright`` and a d3 v7 build: set ``FPL_D3`` to ``d3.min.js`` or install
``d3@7.9.0`` where Node can resolve it. Run with ``uv run pytest -m ui``; set
``FPL_UI_SHOTS=DIR`` to also save screenshots.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.dashboard_preview import render

HERE = Path(__file__).parent
DATA = HERE.parent / "fixtures" / "dashboard"

pytestmark = pytest.mark.ui


def _node_env() -> dict:
    env = dict(os.environ)
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    if root:
        env["NODE_PATH"] = os.pathsep.join(p for p in (env.get("NODE_PATH"), root) if p)
    return env


def _d3() -> Path | None:
    if os.environ.get("FPL_D3"):
        return Path(os.environ["FPL_D3"])
    r = subprocess.run(["node", "-e", "console.log(require.resolve('d3/dist/d3.min.js'))"],
                       capture_output=True, text=True, env=_node_env())
    return Path(r.stdout.strip()) if r.returncode == 0 else None


def test_dashboard_page(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    env = _node_env()
    if subprocess.run(["node", "-e", "require('playwright')"], env=env).returncode != 0:
        pytest.skip("playwright (node) not installed")
    d3 = _d3()
    if d3 is None or not d3.exists():
        pytest.skip("d3 not found: set FPL_D3")
    page = tmp_path / "page.html"
    page.write_text(render(DATA, d3, set(), {"squad_view": "formation"}))
    shots = os.environ.get("FPL_UI_SHOTS", "")
    cmd = ["node", str(HERE / "check_dashboard.js"), str(page), *([shots] if shots else [])]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
