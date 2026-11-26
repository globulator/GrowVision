import re
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[2] / "clients" / "web"

EMOJI_RE = re.compile(
    "[\U0001f300-\U0001f9ff\U0001fa00-\U0001faff\u2600-\u26ff\u2700-\u27bf]"
)


def test_climate_web_ui_elements_and_invariants():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    climate_js = (WEB_DIR / "js" / "climate.js").read_text(encoding="utf-8")
    api_js = (WEB_DIR / "js" / "api.js").read_text(encoding="utf-8")
    app_js = (WEB_DIR / "js" / "app.js").read_text(encoding="utf-8")
    style_css = (WEB_DIR / "style.css").read_text(encoding="utf-8")

    for req_id in (
        'id="btn-climate-menu"',
        'id="modal-climate"',
        'id="climate-esp-badge"',
        'id="climate-mode-control"',
        'id="climate-water-toggle"',
        'id="climate-fan-toggle"',
        'id="climate-schedule-form"',
        'id="climate-ph-form"',
        'id="climate-chart-temp"',
        'id="climate-chart-activity"',
    ):
        assert req_id in html, f"Не найден элемент {req_id} в index.html"

    assert "getClimateSummary" in api_js
    assert "initClimate" in app_js
    assert ".climate-body" in style_css

    for name, content in (
        ("index.html", html),
        ("js/climate.js", climate_js),
        ("js/api.js", api_js),
        ("js/app.js", app_js),
    ):
        assert not EMOJI_RE.search(content), f"Найден Unicode-эмодзи в {name}"
        assert "window.alert" not in content and "window.confirm" not in content
