import logging

import pytest

from app.middleware.tracking import emit_workflow_turn, track_llm_call, track_tool_call


def test_turn_reasons_redact_pii_and_credentials_before_clipping(caplog):
    private = [
        "customer@example.test", "213-555-0199", "4111-1111-1111-1111",
        "123-45-6789", "192.0.2.10", "synthetic-value",
    ]
    with caplog.at_level(logging.INFO, logger="tracking"):
        emit_workflow_turn({
            "supervisor_reason": " ".join(private[:-1]) + " password=" + private[-1] + " " + ("x" * 300),
            "guardian_reason": ("x" * 197) + " customer@example.test",
        })

    record = caplog.records[-1]
    for reason in (record.supervisor_reason, record.guardian_reason):
        assert len(reason) <= 200
        assert not any(value in reason for value in private)
    assert "[REDACTED]" in record.supervisor_reason
    assert "cu" != record.guardian_reason[-2:]


def test_tool_preview_and_llm_errors_omit_input_values(caplog):
    @track_tool_call("synthetic_tool")
    def tool(description, *, email):
        raise ValueError(f"{description}: {email}")

    description = "Call 213-555-0199"
    email = "customer@example.test"
    with caplog.at_level(logging.INFO, logger="tracking"):
        with pytest.raises(ValueError):
            tool(description, email=email)
        with pytest.raises(ValueError):
            with track_llm_call("deepseek.v3.2", "synthetic_node", description):
                raise ValueError(email)

    tool_log, llm_log = caplog.records
    assert tool_log.args_preview == "positional=1, named=email, values=[REDACTED]"
    assert tool_log.error == llm_log.error == "ValueError"
    assert description not in str(tool_log.__dict__)
    assert email not in str(tool_log.__dict__) + str(llm_log.__dict__)
