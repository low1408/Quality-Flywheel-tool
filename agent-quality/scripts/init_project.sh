#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/init_project.sh [options]

Initialize the Agent Quality project from a fresh checkout.

Options:
  --repo PATH          Project root to initialize. Defaults to this repository.
  --python PATH        Python executable to use. Defaults to python3.
  --venv PATH          Virtual environment path. Defaults to <repo>/.venv.
  --no-venv           Do not create/use a virtual environment.
  --skip-install      Do not run pip install -e .
  --skip-smoke        Do not run CLI smoke checks.
  -h, --help          Show this help.

The script is idempotent. It creates .agent-quality config through aq init and
stores smoke-test runtime data under .agent-quality/local.
USAGE
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd -- "${script_dir}/.." && pwd)"
python_bin="python3"
venv_path=""
use_venv=1
install_package=1
run_smoke=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo="$(cd -- "$2" && pwd)"
      shift 2
      ;;
    --python)
      python_bin="$2"
      shift 2
      ;;
    --venv)
      venv_path="$2"
      shift 2
      ;;
    --no-venv)
      use_venv=0
      shift
      ;;
    --skip-install)
      install_package=0
      shift
      ;;
    --skip-smoke)
      run_smoke=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${repo}/pyproject.toml" || ! -d "${repo}/src/agent_quality" ]]; then
  echo "not an Agent Quality project root: ${repo}" >&2
  exit 1
fi
cd "${repo}"

if [[ -z "${venv_path}" ]]; then
  venv_path="${repo}/.venv"
fi

if [[ ${use_venv} -eq 1 ]]; then
  if [[ ! -x "${venv_path}/bin/python" ]]; then
    echo "creating virtual environment: ${venv_path}"
    "${python_bin}" -m venv "${venv_path}"
  fi
  python_bin="${venv_path}/bin/python"
fi

echo "using python: $(${python_bin} -c 'import sys; print(sys.executable)')"

if [[ ${install_package} -eq 1 ]]; then
  echo "installing package in editable mode"
  "${python_bin}" -m pip install -e "${repo}"
fi

aq_home="${repo}/.agent-quality/local"
mkdir -p "${aq_home}"
export AGENT_QUALITY_HOME="${aq_home}"

echo "initializing project config"
PYTHONPATH="${repo}/src" "${python_bin}" -m agent_quality.cli init --repo "${repo}"

if [[ ${run_smoke} -eq 1 ]]; then
  echo "running source compile check"
  PYTHONPATH="${repo}/src" "${python_bin}" -m compileall -q "${repo}/src" "${repo}/tests"

  if PYTHONPATH="${repo}/src" "${python_bin}" -m pytest -q "${repo}/tests" >/tmp/agent-quality-pytest.log 2>&1; then
    echo "pytest passed"
  else
    echo "pytest unavailable or failed; running built-in smoke assertions"
    PYTHONPATH="${repo}/src" "${python_bin}" - <<'PY'
from agent_quality.adapters.codex_cli import rows_from_jsonl
from agent_quality.config import load_verify_config, verifier_commands
from agent_quality.privacy.redaction import redact_json
from pathlib import Path

redacted = redact_json({"token": "abc", "text": "sk-abcdefghijklmnopqrstuvwxyz123456"})
assert redacted.value["token"] == "[REDACTED:field]"
events = rows_from_jsonl(['{"type":"exec.completed","command":"pytest -q","exit_code":0}'], run_id="run_smoke")
assert events[0]["event_type"] == "agent.tool.completed"
config = load_verify_config(Path("examples/verify.yaml"))
assert verifier_commands(config)
PY
  fi

  echo "running CLI smoke task"
  PYTHONPATH="${repo}/src" "${python_bin}" -m agent_quality.cli run \
    --repo "${repo}" \
    --allow-dirty \
    --skip-review \
    "initializer smoke run" \
    --agent-command "${python_bin}" -c 'import json; print(json.dumps({"type":"exec.completed","command":"pytest -q","exit_code":0,"duration_ms":1}))'
fi

cat <<EOF

Initialization complete.

Project config: ${repo}/.agent-quality
Runtime data:    ${AGENT_QUALITY_HOME}
EOF

if [[ ${use_venv} -eq 1 ]]; then
  cat <<EOF

Try:
  source "${venv_path}/bin/activate"
  aq report summary
EOF
else
  cat <<EOF

Try:
  AGENT_QUALITY_HOME="${AGENT_QUALITY_HOME}" PYTHONPATH="${repo}/src" ${python_bin} -m agent_quality.cli report summary
EOF
fi
