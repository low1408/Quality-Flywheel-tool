"""Shared connection and redaction primitives for Agent Quality persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import agent_quality.db as aq_db
import agent_quality.privacy.redaction as aq_redact


class AQAdapterBase:
    """Connection and privacy helpers shared by AQ adapter capabilities."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        """Connect to the agent-quality SQLite database."""
        return aq_db.connect(self.db_path)

    def _redact_text(self, text: str | None) -> str:
        if not text:
            return ""
        return aq_redact.redact_text(text).value

    def _redact_dict(self, data: dict[str, Any] | None) -> dict[str, Any]:
        if not data:
            return {}
        return aq_redact.redact_json(data).value
