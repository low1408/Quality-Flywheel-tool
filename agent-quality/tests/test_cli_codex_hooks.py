import json

from agent_quality.cli import _install_codex_hooks


def test_install_codex_hooks_targets_git_root_from_nested_repo(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "agent-quality"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    _install_codex_hooks(nested, "python3")

    root_hooks = repo / ".codex" / "hooks.json"
    nested_hooks = nested / ".codex" / "hooks.json"
    config = repo / ".codex" / "config.toml"

    assert root_hooks.exists()
    assert not nested_hooks.exists()
    assert "hooks = true" in config.read_text(encoding="utf-8")

    hooks = json.loads(root_hooks.read_text(encoding="utf-8"))
    command = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert f"AGENT_QUALITY_HOME={repo / '.agent-quality' / 'local'}" in command


def test_install_codex_hooks_preserves_existing_project_config(tmp_path):
    repo = tmp_path / "repo"
    codex_dir = repo / ".codex"
    codex_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (codex_dir / "config.toml").write_text(
        'model = "gpt-5.5"\n\n[features]\ngoals = true\n\n[tui]\ntheme = "light"\n',
        encoding="utf-8",
    )

    _install_codex_hooks(repo, "python3")

    config = (codex_dir / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5.5"' in config
    assert "[features]\nhooks = true\ngoals = true" in config
    assert '[tui]\ntheme = "light"' in config
