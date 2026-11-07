"""Adding a widget = one .py + one .html in config/widgets/, no core changes."""

from __future__ import annotations

import respx
from fastapi.testclient import TestClient

from app.main import create_app
from app.runtime import Paths

PLUGIN = '''
from typing import Any
from app.widgets.base import BaseWidget, WidgetOptions

class StarsOptions(WidgetOptions):
    repository: str

class Widget(BaseWidget):
    type = "github-repo"
    Options = StarsOptions
    default_cache = "30m"

    async def fetch(self) -> dict[str, Any]:
        repo = await self.get_json(f"https://api.github.com/repos/{self.options.repository}")
        return {"stars": repo["stargazers_count"]}
'''

TEMPLATE = '<p class="stars">{{ o.repository }}: {{ stars | compact_number }} stars</p>\n'

CONFIG = """
pages:
  - name: Home
    columns:
      - size: full
        widgets:
          - type: github-repo
            repository: glanceapp/glance
          - type: github-repo
            repo: typo
"""


def test_plugin_folder_widget(paths: Paths) -> None:
    plugins = paths.config_dir / "widgets"
    plugins.mkdir()
    (plugins / "github_repo.py").write_text(PLUGIN, encoding="utf-8")
    (plugins / "github-repo.html").write_text(TEMPLATE, encoding="utf-8")
    paths.config_file.write_text(CONFIG, encoding="utf-8")

    with TestClient(create_app(paths, watch=False)) as c, respx.mock(assert_all_called=False) as m:
        m.get("https://api.github.com/repos/glanceapp/glance").respond(json={"stargazers_count": 31234})
        page = c.get("/").text
        assert 'hx-get="/w/home/c0-0"' in page
        assert "repo: unknown option" in page  # plugin options are validated like built-ins
        html = c.get("/w/home/c0-0").text
        assert "glanceapp/glance: 31.2k stars" in html
