import json

from agent_quality.cli import _ingest
from agent_quality.db import all_rows, connect


def _event(event_id: str) -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": "agent.event",
            "source": {"provider": "test", "source_event_type": "test.event"},
            "data": {},
        }
    )


def test_ingest_continues_after_bad_json_and_duplicates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    source = tmp_path / "events.jsonl"
    source.write_text("\n".join([_event("evt_1"), "{bad json", _event("evt_1"), _event("evt_2")]), encoding="utf-8")

    _ingest(str(source))

    out = capsys.readouterr()
    assert "ingested 2 events, skipped 2" in out.out
    assert "invalid JSON" in out.err
    assert "duplicate event" in out.err
    rows = all_rows(connect(), "SELECT id FROM events ORDER BY id")
    assert [row["id"] for row in rows] == ["evt_1", "evt_2"]
