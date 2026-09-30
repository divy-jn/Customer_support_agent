import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from tests.evals.run_baseline import evaluate_example_with_retry, run_baseline

class DummyExample:
    def __init__(self, id_str):
        self.id = id_str
        self.execution_mode = "READ_ONLY"

@pytest.mark.asyncio
async def test_evaluate_example_with_retry_transport_failure():
    """Test that a transport failure is retried and eventually fails if it exceeds retries."""
    mock_supabase = MagicMock()
    example = DummyExample("test_1")
    sem = asyncio.Semaphore(1)
    
    with patch("tests.evals.run_baseline.evaluate_example") as mock_eval:
        # Simulate LLM transport failure in state
        mock_eval.return_value = {
            "example_id": "test_1",
            "status": "FAIL",
            "predicted_intent": "general",
            "selected_agent": "rag_agent",
            "llm_transport_failure": True,
            "parser_failure": False,
            "router_error_type": "timeout"
        }
        
        # Override asyncio.sleep to not actually sleep during tests
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            res = await evaluate_example_with_retry(example, mock_supabase, sem, max_retries=2)
            
            assert mock_eval.call_count == 3  # 1 initial + 2 retries
            assert mock_sleep.call_count == 2
            assert res["retries"] == 2
            assert res["llm_transport_failure"] is True
            assert "latency" in res

@pytest.mark.asyncio
async def test_evaluate_example_with_retry_401_no_retry():
    """Test that a 401 Unauthorized is not retried."""
    mock_supabase = MagicMock()
    example = DummyExample("test_401")
    sem = asyncio.Semaphore(1)
    
    with patch("tests.evals.run_baseline.evaluate_example") as mock_eval:
        mock_eval.return_value = {
            "example_id": "test_401",
            "status": "FAIL",
            "llm_transport_failure": True,
            "router_error_type": "401 Unauthorized"
        }
        
        res = await evaluate_example_with_retry(example, mock_supabase, sem, max_retries=2)
        assert mock_eval.call_count == 1
        assert res["retries"] == 0

@pytest.mark.asyncio
async def test_evaluate_example_with_retry_403_no_retry():
    """Test that a 403 Forbidden is not retried."""
    mock_supabase = MagicMock()
    example = DummyExample("test_403")
    sem = asyncio.Semaphore(1)
    
    with patch("tests.evals.run_baseline.evaluate_example") as mock_eval:
        # Simulate exception thrown instead of returned payload
        mock_eval.side_effect = Exception("403 Forbidden")
        
        res = await evaluate_example_with_retry(example, mock_supabase, sem, max_retries=2)
        assert mock_eval.call_count == 1
        assert res["retries"] == 0

@pytest.mark.asyncio
async def test_evaluate_example_with_retry_429_retryable():
    """Test that a 429 RateLimit is retried."""
    mock_supabase = MagicMock()
    example = DummyExample("test_429")
    sem = asyncio.Semaphore(1)
    
    with patch("tests.evals.run_baseline.evaluate_example") as mock_eval:
        mock_eval.return_value = {
            "example_id": "test_429",
            "status": "FAIL",
            "llm_transport_failure": True,
            "router_error_type": "429 Too Many Requests"
        }
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            res = await evaluate_example_with_retry(example, mock_supabase, sem, max_retries=1)
            assert mock_eval.call_count == 2
            assert res["retries"] == 1

@pytest.mark.asyncio
async def test_evaluate_example_with_retry_success():
    """Test that a successful run does not retry."""
    mock_supabase = MagicMock()
    example = DummyExample("test_2")
    sem = asyncio.Semaphore(1)
    
    with patch("tests.evals.run_baseline.evaluate_example") as mock_eval:
        mock_eval.return_value = {
            "example_id": "test_2",
            "status": "PASS",
            "llm_transport_failure": False,
            "parser_failure": False
        }
        
        res = await evaluate_example_with_retry(example, mock_supabase, sem, max_retries=2)
        assert mock_eval.call_count == 1
        assert res["retries"] == 0

@pytest.mark.asyncio
async def test_evaluate_example_exception():
    """Test that arbitrary top-level transport exceptions are retried."""
    mock_supabase = MagicMock()
    example = DummyExample("test_3")
    sem = asyncio.Semaphore(1)
    
    with patch("tests.evals.run_baseline.evaluate_example") as mock_eval:
        # 2 timeouts, then success
        mock_eval.side_effect = [
            Exception("httpx.TimeoutException"),
            Exception("502 Bad Gateway"),
            {
                "example_id": "test_3",
                "status": "PASS",
                "llm_transport_failure": False,
                "parser_failure": False
            }
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            res = await evaluate_example_with_retry(example, mock_supabase, sem, max_retries=3)
            assert mock_eval.call_count == 3
            assert res["status"] == "PASS"
            assert res["retries"] == 2

@pytest.mark.asyncio
async def test_concurrency_never_exceeds_maximum():
    """Verify that execution is bounded by the semaphore."""
    mock_supabase = MagicMock()
    sem = asyncio.Semaphore(2)
    
    active_evals = 0
    max_active_evals = 0
    
    async def fake_eval_example(*args, **kwargs):
        nonlocal active_evals, max_active_evals
        active_evals += 1
        max_active_evals = max(max_active_evals, active_evals)
        await asyncio.sleep(0.01) # Yield to event loop
        active_evals -= 1
        return {"example_id": "fake", "status": "PASS", "llm_transport_failure": False}
        
    with patch("tests.evals.run_baseline.evaluate_example", new=fake_eval_example):
        examples = [DummyExample(str(i)) for i in range(10)]
        tasks = [evaluate_example_with_retry(ex, mock_supabase, sem, 0) for ex in examples]
        results = await asyncio.gather(*tasks)
        
        assert max_active_evals == 2
        assert len(results) == 10
        for r in results:
            assert r["status"] == "PASS"
