import os
import sys
import gzip
import shutil
import logging
import uuid
import json
import contextvars
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timezone

# ──────────────────────────────────────────────
#  Trace / Correlation ID (per-request context)
# ──────────────────────────────────────────────
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """Get the current trace ID from context, or generate a new one."""
    tid = trace_id_var.get()
    if not tid:
        tid = uuid.uuid4().hex[:12]
        trace_id_var.set(tid)
    return tid


def set_trace_id(tid: str | None = None) -> str:
    """Set a trace ID in the current context. Returns the trace ID."""
    tid = tid or uuid.uuid4().hex[:12]
    trace_id_var.set(tid)
    return tid


# ──────────────────────────────────────────────
#  Compressed Rotating File Handler
# ──────────────────────────────────────────────
class CompressedTimedRotatingFileHandler(TimedRotatingFileHandler):
    """TimedRotatingFileHandler that compresses rotated logs with gzip."""

    def doRollover(self):
        super().doRollover()
        # Compress the most recently rotated file
        if self.backupCount > 0:
            log_dir = os.path.dirname(self.baseFilename)
            try:
                for fn in os.listdir(log_dir):
                    fp = os.path.join(log_dir, fn)
                    if fp.startswith(self.baseFilename + ".") and not fp.endswith(".gz"):
                        try:
                            with open(fp, "rb") as f_in:
                                with gzip.open(fp + ".gz", "wb") as f_out:
                                    shutil.copyfileobj(f_in, f_out)
                            os.remove(fp)
                        except (OSError, PermissionError):
                            pass
            except (OSError, PermissionError):
                pass


# ──────────────────────────────────────────────
#  JSON Formatter
# ──────────────────────────────────────────────
class JSONFormatter(logging.Formatter):
    """Format logs as JSON for production log aggregators (ELK, Datadog, etc.)"""

    # Fields that are part of the default LogRecord — skip when adding extras
    _SKIP_FIELDS = frozenset({
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
    })

    def format(self, record):
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": get_trace_id(),
        }

        # Include exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # Include extra fields (like session_id, customer_id) if passed via logger.info("...", extra={...})
        for key, value in record.__dict__.items():
            if key not in self._SKIP_FIELDS:
                try:
                    json.dumps(value)
                    log_obj[key] = value
                except TypeError:
                    log_obj[key] = str(value)

        return json.dumps(log_obj)


# ──────────────────────────────────────────────
#  Console Formatter (with trace ID)
# ──────────────────────────────────────────────
class TraceConsoleFormatter(logging.Formatter):
    """Human-readable console format that includes trace_id."""

    def format(self, record):
        tid = get_trace_id()
        prefix = f"[{tid}] " if tid else ""
        return (
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} | "
            f"{record.levelname:<8s} | {prefix}{record.name} | {record.getMessage()}"
        )


# ──────────────────────────────────────────────
#  Setup
# ──────────────────────────────────────────────
def setup_logger(log_level: str = "INFO"):
    """Setup production-grade logging with rotating files and JSON formatting.

    Args:
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    """
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, "intellisupport.log")

    level = getattr(logging, log_level.upper(), logging.INFO)

    # Root logger
    logger = logging.getLogger()
    logger.setLevel(level)

    # Clear existing handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    # 1. Console Handler (Standard Output) — Human Readable with trace_id
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(TraceConsoleFormatter())

    # 2. File Handler (Rotating + Compressed) — JSON Formatted
    # Rotates at midnight, keeps 30 days of history, compresses old files
    file_handler = CompressedTimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JSONFormatter())

    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Silence chatty libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

    return logger


# ──────────────────────────────────────────────
#  Log Reader (for Admin Dashboard)
# ──────────────────────────────────────────────
def get_recent_logs(
    lines: int = 100,
    level_filter: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """Read recent logs from the JSON log file for the Admin Dashboard.

    Args:
        lines: Max number of log entries to return.
        level_filter: Optional filter — only return logs at this level (INFO, WARNING, ERROR).
        search: Optional keyword search across message and logger fields.
    """
    log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "intellisupport.log")

    if not os.path.exists(log_file):
        return []

    logs = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-(lines * 3):]  # Read extra to account for filters

            for line in recent_lines:
                try:
                    if not line.strip():
                        continue
                    entry = json.loads(line)

                    # Apply level filter
                    if level_filter and entry.get("level", "").upper() != level_filter.upper():
                        continue

                    # Apply search filter
                    if search:
                        search_lower = search.lower()
                        searchable = f"{entry.get('message', '')} {entry.get('logger', '')} {entry.get('trace_id', '')}".lower()
                        if search_lower not in searchable:
                            continue

                    logs.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to read logs: {e}")

    # Return newest first, capped at requested count
    return logs[-lines:][::-1]
