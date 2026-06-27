from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = PROJECT_ROOT / "vscode-extension" / "media"
STATIC_DIR = PROJECT_ROOT / "src" / "agent_quality" / "collector" / "static"


@pytest.mark.parametrize("asset", ["dashboard.html", "dashboard.css", "dashboard.js"])
def test_dashboard_assets_stay_synchronized(asset):
    assert (MEDIA_DIR / asset).read_bytes() == (STATIC_DIR / asset).read_bytes()


def test_dashboard_keeps_machine_fields_out_of_the_primary_ui():
    source = (MEDIA_DIR / "dashboard.js").read_text(encoding="utf-8")
    render_runs = source.split("function renderRuns()", 1)[1].split("function filteredRuns()", 1)[0]
    filtered_runs = source.split("function filteredRuns()", 1)[1].split("function renderDetail(", 1)[0]
    overview = source.split("function renderOverview(", 1)[1].split("function renderVerifiers(", 1)[0]

    assert 'class="run-id"' not in render_runs
    assert 'data-run-id="${escapeAttr(run.id)}"' in render_runs
    assert "run.id," in filtered_runs

    for token_label in ("Input tokens", "Cached input", "Output tokens"):
        assert token_label not in overview

    prompt_position = overview.index('aria-labelledby="prompt-heading"')
    output_position = overview.index('aria-labelledby="output-heading"')
    reasoning_position = overview.index('aria-labelledby="reasoning-heading"')
    tools_position = overview.index('aria-labelledby="tools-heading"')
    details_position = overview.index('<details class="overview-secondary">')
    assert prompt_position < output_position < reasoning_position < tools_position < details_position
    assert "Private chain-of-thought remains encrypted" in overview
