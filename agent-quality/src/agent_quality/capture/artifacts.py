from __future__ import annotations

from pathlib import Path

from agent_quality.hashutil import sha256_file
from agent_quality.ids import new_id
from agent_quality.paths import artifacts_root


def run_artifact_dir(run_id: str) -> Path:
    path = artifacts_root() / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_artifact(run_id: str, name: str, content: str | bytes) -> tuple[str, Path, str, int]:
    path = run_artifact_dir(run_id) / name
    if isinstance(content, bytes):
        try:
            # Try to decode to UTF-8 to apply text redaction if it's textual
            decoded = content.decode("utf-8")
            from agent_quality.privacy.redaction import redact_text
            content = redact_text(decoded).value.encode("utf-8")
        except (UnicodeDecodeError, ValueError):
            pass  # Treat as binary, do not redact
    else:
        from agent_quality.privacy.redaction import redact_text
        content = redact_text(content).value

    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return new_id("art"), path, sha256_file(path), path.stat().st_size

