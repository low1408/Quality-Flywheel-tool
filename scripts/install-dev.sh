#!/bin/sh
set -eu

repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
venv_path=${QUALITY_FLYWHEEL_VENV:-"${HOME}/venvs/quality-flywheel"}
task_python=${PYTHON:-"${venv_path}/bin/python"}

if [ ! -x "$task_python" ] && ! command -v "$task_python" >/dev/null 2>&1; then
    echo "Python executable not found: $task_python" >&2
    echo "Set PYTHON or QUALITY_FLYWHEEL_VENV to select the development interpreter." >&2
    exit 2
fi

"$task_python" -m pip install -e "$repository_root/agent-quality[dev]" -e "$repository_root/kimi_coding_agent_flywheel[test]"
