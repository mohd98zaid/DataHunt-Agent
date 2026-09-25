import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict

# Secret and PII patterns for redaction
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
API_KEY_PATTERN = re.compile(r"(?i)(key|token|secret|password|auth|authorization)[\"']?\s*[:=]\s*[\"']?([a-zA-Z0-9_\-\.]{8,})[\"']?")
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]+")

def redact_sensitive(text: str) -> str:
    """Redact secrets and PII from a string."""
    if not isinstance(text, str):
        return text
    # Redact Bearer tokens
    text = BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    # Redact API keys / secrets
    text = API_KEY_PATTERN.sub(r"\1: [REDACTED]", text)
    # Redact personal emails
    text = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", text)
    # Redact phone numbers
    text = PHONE_PATTERN.sub("[PHONE_REDACTED]", text)
    return text

def sanitize_value(val: Any) -> Any:
    if isinstance(val, str):
        if len(val) > 2000:
            return redact_sensitive(val[:2000]) + " ... [TRUNCATED]"
        return redact_sensitive(val)
    elif isinstance(val, dict):
        return {k: sanitize_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_value(v) for v in val]
    return val

class JsonFormatter(logging.Formatter):
    """Formats log records as structured JSON with redactions."""
    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
        }
        # Include extra attributes attached to the record
        if hasattr(record, "event"):
            log_obj["event"] = record.event
        if hasattr(record, "task_id"):
            log_obj["task_id"] = record.task_id
        if hasattr(record, "run_id"):
            log_obj["run_id"] = record.run_id
        if hasattr(record, "tool"):
            log_obj["tool"] = record.tool
        if hasattr(record, "duration_ms"):
            log_obj["duration_ms"] = record.duration_ms
        if hasattr(record, "status"):
            log_obj["status"] = record.status
        if hasattr(record, "source_domain"):
            log_obj["source_domain"] = record.source_domain
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_obj["data"] = sanitize_value(record.extra_data)
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_obj, ensure_ascii=True)

def get_logger(name: str = "datahunt") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger

logger = get_logger("datahunt")
