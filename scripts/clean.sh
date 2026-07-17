#!/bin/sh
set -eu

repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

# Git metadata and local telemetry are excluded from traversal and preserved.
for project_root in \
    "$repository_root/agent-quality" \
    "$repository_root/kimi_coding_agent_flywheel"
do
    find "$project_root" \
        \( -path '*/.git' -o -path '*/.agent-quality/local' \) -prune -o \
        -type d \( -name __pycache__ -o -name .pytest_cache -o -name '*.egg-info' \) \
        -print -exec rm -rf -- {} +
    find "$project_root" \
        \( -path '*/.git' -o -path '*/.agent-quality/local' \) -prune -o \
        -type f -name '*.pyc' -print -exec rm -f -- {} +
    for generated_dir in "$project_root/build" "$project_root/dist"
    do
        if [ -d "$generated_dir" ]; then
            printf '%s\n' "$generated_dir"
            rm -rf -- "$generated_dir"
        fi
    done
done

if [ -d "$repository_root/.pytest_cache" ]; then
    printf '%s\n' "$repository_root/.pytest_cache"
    rm -rf -- "$repository_root/.pytest_cache"
fi

find "$repository_root/agent-quality/vscode-extension" \
    -maxdepth 1 -type f -name '*.vsix' -print -delete

# These are ignored packaging copies generated from collector/static. The
# canonical browser assets and every .agent-quality/local tree remain untouched.
for generated_asset in dashboard.css dashboard.html dashboard.js
do
    generated_path="$repository_root/agent-quality/vscode-extension/media/$generated_asset"
    if [ -f "$generated_path" ]; then
        printf '%s\n' "$generated_path"
        rm -- "$generated_path"
    fi
done
