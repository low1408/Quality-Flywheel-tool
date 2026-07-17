#!/bin/sh
set -eu

repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
venv_path=${QUALITY_FLYWHEEL_VENV:-"${HOME}/venvs/quality-flywheel"}
task_python=${PYTHON:-"${venv_path}/bin/python"}
selection=${1:-all}

if [ ! -x "$task_python" ] && ! command -v "$task_python" >/dev/null 2>&1; then
    echo "Python executable not found: $task_python" >&2
    echo "Set PYTHON or QUALITY_FLYWHEEL_VENV to select the development interpreter." >&2
    exit 2
fi

run_agent_quality() {
    (
        cd "$repository_root/agent-quality"
        PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$task_python" -m pytest
    )
}

run_flywheel() {
    (
        cd "$repository_root/kimi_coding_agent_flywheel"
        PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$task_python" -m pytest
    )
}

run_extension() {
    (
        cd "$repository_root/agent-quality/vscode-extension"
        npm run check
        npm run validate-package
    )
}

run_scripts() {
    for script in \
        "$repository_root"/scripts/*.sh \
        "$repository_root"/agent-quality/scripts/*.sh
    do
        sh -n "$script"
    done
}

case "$selection" in
    agent-quality) run_agent_quality ;;
    flywheel) run_flywheel ;;
    extension) run_extension ;;
    all)
        run_scripts
        run_agent_quality
        run_flywheel
        run_extension
        ;;
    *)
        echo "Unknown check selection: $selection" >&2
        echo "Expected agent-quality, flywheel, extension, or all." >&2
        exit 2
        ;;
esac
