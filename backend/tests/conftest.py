import pytest

def pytest_configure(config):
    config.addinivalue_line(
        "markers", 
        "llm_eval: marks tests that require a live LLM (deselect with '-m \"not llm_eval\"')"
    )

# ── Deterministic LLM gating ──────────────────────────────
# A session-scoped probe checks whether the configured LLM provider
# accepts authentication. If the probe fails with a 401/auth error,
# all @pytest.mark.llm_eval tests are automatically skipped with an
# explicit reason.
#
# IMPORTANT: Only authentication/credential failures trigger a skip.
# Timeouts, DNS errors, malformed responses, and application bugs
# must NOT be silently converted into skips — they propagate as
# real test failures.

_llm_available = None
_llm_skip_reason = None

def _probe_llm_connectivity() -> bool:
    """One-shot probe: can we reach the configured LLM without auth errors?"""
    global _llm_available, _llm_skip_reason
    if _llm_available is not None:
        return _llm_available
    try:
        from app.llm_factory import get_llm
        llm = get_llm(temperature=0)
        # Minimal probe — a single token completion
        llm.invoke("Say OK")
        _llm_available = True
    except Exception as e:
        err_str = str(e).lower()
        err_type = type(e).__name__.lower()
        # Only skip on clear authentication/credential failures
        is_auth_failure = (
            "401" in err_str
            or "unauthorized" in err_str
            or "authentication" in err_type
            or "authenticationerror" in err_type
        )
        if is_auth_failure:
            _llm_available = False
            _llm_skip_reason = f"LLM authentication failed: {type(e).__name__}: {e}"
        else:
            # Non-auth errors must NOT be silently skipped.
            # Mark LLM as available so the test runs and the real error surfaces.
            _llm_available = True
    return _llm_available

def pytest_runtest_setup(item):
    """Auto-skip llm_eval tests when the LLM provider rejects authentication."""
    markers = list(item.iter_markers(name="llm_eval"))
    if markers:
        if not _probe_llm_connectivity():
            pytest.skip(_llm_skip_reason or "LLM authentication failed — skipping live LLM eval")
