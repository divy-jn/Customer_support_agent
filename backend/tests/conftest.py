import pytest

def pytest_configure(config):
    config.addinivalue_line(
        "markers", 
        "llm_eval: marks tests that require a live LLM (deselect with '-m \"not llm_eval\"')"
    )
