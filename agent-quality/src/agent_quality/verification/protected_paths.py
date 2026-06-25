from __future__ import annotations

import fnmatch
from pathlib import Path


def changed_paths_from_name_status(name_status: str) -> list[str]:
    paths: list[str] = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            paths.append(parts[-1])
    return paths


def protected_patterns(config: dict) -> list[str]:
    values = config.get("protected_paths")
    return [str(value) for value in values] if isinstance(values, list) else []


def protected_violations(paths: list[str], patterns: list[str]) -> list[str]:
    return [path for path in paths for pattern in patterns if fnmatch.fnmatch(path, pattern)]
