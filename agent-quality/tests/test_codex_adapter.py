from agent_quality.adapters.codex_cli import rows_from_jsonl


def test_maps_command_completion():
    rows = rows_from_jsonl(
        ['{"type":"exec.completed","command":"pytest -q","exit_code":0,"duration_ms":12}'],
        run_id="run_1",
        session_id="ses_1",
    )
    assert rows[0]["event_type"] == "agent.tool.completed"
    assert rows[0]["tool_category"] == "test"
    assert rows[0]["exit_code"] == 0
    assert rows[0]["run_id"] == "run_1"
