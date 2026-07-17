from pathlib import Path
import tomllib

from kimi_coding_agent_flywheel import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_distribution_declares_and_contains_its_mit_license():
    metadata = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert metadata["license"] == "MIT"
    assert metadata["license-files"] == ["LICENSE"]
    assert license_text.startswith("MIT License\n")
    assert "The above copyright notice and this permission notice" in license_text


def test_distribution_version_matches_package():
    metadata = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert metadata["version"] == __version__
    assert "agent-quality>=0.2.0" in metadata["dependencies"]
