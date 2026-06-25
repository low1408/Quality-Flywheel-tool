from __future__ import annotations

import os
from pathlib import Path


def default_home() -> Path:
    return Path(os.environ.get("AGENT_QUALITY_HOME", "~/.agent-quality")).expanduser()


def default_db_path() -> Path:
    return default_home() / "quality.sqlite3"


def artifacts_root() -> Path:
    return default_home() / "artifacts"


def ensure_home() -> Path:
    home = default_home()
    for child in (home, artifacts_root(), home / "cases", home / "reports", home / "spool"):
        child.mkdir(parents=True, exist_ok=True)
    return home
