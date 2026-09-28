"""Static web-app export for simdref.

Assembles the self-contained single-page app (``index.html``) from the
template files under ``simdref/templates/`` and delegates the JSON data
export to :mod:`simdref.export`, the module a separate web repo would
consume without this HTML step (see ``simdref export``).
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from simdref.export import export_site_data
from simdref.models import Catalog
from simdref.ui_labels import as_json_dict as _ui_labels_payload


def _load_template() -> str:
    """Assemble the HTML template shell + CSS + JS source files.

    Injects the ``UI_LABELS`` + ``KEYMAP`` ``window.SIMDREF_UI`` JSON blob
    before the app script so the SPA and the TUI share the same vocabulary.
    """
    tpl = resources.files("simdref.templates")
    html = tpl.joinpath("index.html").read_text()
    css = tpl.joinpath("style.css").read_text()
    js = tpl.joinpath("app.js").read_text()
    ui_blob = json.dumps(_ui_labels_payload(), separators=(",", ":"))
    js_with_labels = f"window.SIMDREF_UI = {ui_blob};\n{js}"
    return html.replace("/* __CSS__ */", css).replace("/* __JS__ */", js_with_labels)


def export_web(catalog: Catalog, web_dir: Path) -> None:
    """Write the web app to *web_dir*: the JSON site data plus ``index.html``.

    See :func:`simdref.export.export_site_data` for the JSON outputs.
    """
    export_site_data(catalog, web_dir)
    (web_dir / "index.html").write_text(_load_template())
