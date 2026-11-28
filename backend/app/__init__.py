"""Initialize SDK tracing before importing application modules."""

import os
from pathlib import Path

from dotenv import dotenv_values


def _configure_tracing_environment(env_file: Path) -> None:
    """Publish only tracing settings from the backend dotenv file to the SDK."""
    file_values = dotenv_values(env_file)
    for setting in (
        "TRACING", "TRACING_V2", "API_KEY", "PROJECT", "ENDPOINT",
        "WORKSPACE_ID", "HIDE_INPUTS", "HIDE_OUTPUTS",
    ):
        current, legacy = f"LANGSMITH_{setting}", f"LANGCHAIN_{setting}"
        if current in os.environ or legacy in os.environ:
            continue
        value = file_values.get(current, file_values.get(legacy))
        if value is not None:
            os.environ[current] = value

    # Traces contain execution metadata by default. Chat content requires opt-in.
    for setting in ("HIDE_INPUTS", "HIDE_OUTPUTS"):
        if f"LANGCHAIN_{setting}" not in os.environ:
            os.environ.setdefault(f"LANGSMITH_{setting}", "true")


_configure_tracing_environment(Path(__file__).resolve().parents[1] / ".env")
