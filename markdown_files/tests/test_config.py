from agent_quality.config import load_verify_config, verifier_commands


def test_tiny_yaml_verify_commands(tmp_path):
    path = tmp_path / "verify.yaml"
    path.write_text(
        """
version: 1
acceptance:
  - name: requested
    command: python3 -m pytest -q
    timeout_seconds: 10
""",
        encoding="utf-8",
    )
    config = load_verify_config(path)
    commands = verifier_commands(config)
    assert commands[0]["name"] == "requested"
    assert commands[0]["timeout_seconds"] == 10
