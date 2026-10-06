"""Logging to the console and logs/reconciliation.log, with secret redaction."""

from __future__ import annotations

import logging
import re
from pathlib import Path

LOGGER_NAME = "zoho_reconciliation"
_FORMAT = "%(asctime)s %(levelname)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_SENSITIVE_PAIR = re.compile(r"(?i)\b(password|passwd|otp|token|cookie|authorization|session)\b(\s*[:=]\s*)\S+")


class RedactingFilter(logging.Filter):
    """Replaces known secret values and 'password=...' style pairs with ***."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "***")
        message = _SENSITIVE_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}***", message)
        record.msg = message
        record.args = None
        return True


def setup_logging(level: str, logs_dir: Path, secrets: list[str]) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT, _DATE_FORMAT)
    redactor = RedactingFilter(secrets)

    file_handler = logging.FileHandler(logs_dir / "reconciliation.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redactor)
    logger.addHandler(console_handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
