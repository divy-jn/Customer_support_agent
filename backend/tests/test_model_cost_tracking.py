import logging

from app.middleware.tracking import _estimate_cost, get_aggregate_metrics, track_llm_call


def test_deepseek_standard_bedrock_pricing():
    assert _estimate_cost("deepseek.v3.2", 1_000_000, 1_000_000) == 2.47


def test_unpriced_model_warns_and_does_not_change_known_cost(caplog, monkeypatch):
    from app.middleware import tracking

    llm_metrics = tracking._aggregate_metrics["llm"]
    monkeypatch.setitem(llm_metrics, "unpriced_calls", 0)
    before = get_aggregate_metrics()["llm"]
    with caplog.at_level(logging.INFO, logger="tracking"):
        with track_llm_call("synthetic-unpriced-model", "test"):
            pass

    warning, completion = caplog.records
    assert warning.levelno == logging.WARNING
    assert warning.model == "synthetic-unpriced-model"
    assert completion.estimated_cost_usd is None
    after = get_aggregate_metrics()["llm"]
    assert after["unpriced_calls"] == 1
    assert after["total_estimated_cost_usd"] == before["total_estimated_cost_usd"]
