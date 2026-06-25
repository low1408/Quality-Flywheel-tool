from agent_quality.privacy.redaction import redact_json


def test_redacts_secret_fields_and_tokens():
    result = redact_json({"token": "abc", "text": "use sk-abcdefghijklmnopqrstuvwxyz123456"})
    assert result.value["token"] == "[REDACTED:field]"
    assert "[REDACTED:openai_api_key]" in result.value["text"]
    assert "sensitive_field" in result.findings
