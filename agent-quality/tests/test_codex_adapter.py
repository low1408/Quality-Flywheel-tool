from agent_quality.adapters.codex_cli import rows_from_jsonl
import json


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


def test_maps_structured_mcp_reasoning_and_assistant_items():
    rows = rows_from_jsonl(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "reasoning", "text": "Check the dependency boundary."},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "quorum",
                        "tool": "consult_council",
                        "arguments": {"question": "Review this"},
                        "result": {"content": [{"type": "text", "text": "No defects"}]},
                        "status": "completed",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Review complete."},
                }
            ),
        ],
        run_id="run_trace",
    )

    reasoning = json.loads(rows[0]["normalized_payload"])
    mcp = json.loads(rows[1]["normalized_payload"])
    assistant = json.loads(rows[2]["normalized_payload"])

    assert rows[0]["event_type"] == "agent.reasoning"
    assert reasoning["reasoning"] == "Check the dependency boundary."
    assert rows[1]["event_type"] == "agent.tool.completed"
    assert rows[1]["tool_category"] == "mcp"
    assert mcp["tool_name"] == "quorum/consult_council"
    assert mcp["tool_input"] == {"question": "Review this"}
    assert rows[2]["event_type"] == "agent.message"
    assert assistant["assistant_output"] == "Review complete."
