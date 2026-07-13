"""
Logging configuration for Polymarket client.

Provides structured logging for production use.
"""

import logging
import logging.config
from copy import deepcopy
from typing import Any, Optional

from .utils.structured_logging import (
    CredentialRedactionFilter,
    RedactingJsonFormatter,
)

# Root logger namespace for this package, derived from wherever the package is
# installed (``polymarket`` in the source repo, ``shared.polymarket`` in downstream project).
_PACKAGE = __name__.rpartition(".")[0]


DEFAULT_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "credential_redaction": {"()": CredentialRedactionFilter},
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "detailed": {
            "format": (
                "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d "
                "- %(message)s (%(funcName)s)"
            ),
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {
            "()": RedactingJsonFormatter,
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "standard",
            "filters": ["credential_redaction"],
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "filters": ["credential_redaction"],
            "filename": "polymarket_client.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "ERROR",
            "formatter": "detailed",
            "filters": ["credential_redaction"],
            "filename": "polymarket_errors.log",
            "maxBytes": 10485760,
            "backupCount": 5,
        },
    },
    "loggers": {
        _PACKAGE: {
            "level": "INFO",
            "handlers": ["console", "file", "error_file"],
            "propagate": False,
        }
    },
    "root": {"level": "WARNING", "handlers": ["console"]},
}


def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    json_format: bool = False,
) -> None:
    """
    Setup logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file path
        json_format: Use JSON formatting
    """
    # setup_logging may be called more than once by test/runtime bootstraps.
    # A deep copy prevents one JSON/file override from mutating future calls.
    config: dict[str, Any] = deepcopy(DEFAULT_LOGGING_CONFIG)

    # Override level
    if level:
        config["loggers"][_PACKAGE]["level"] = level.upper()

    # Override log file
    if log_file:
        config["handlers"]["file"]["filename"] = log_file
        error_file = log_file.replace(".log", "_errors.log")
        config["handlers"]["error_file"]["filename"] = error_file

    # Use JSON formatter
    if json_format:
        config["handlers"]["console"]["formatter"] = "json"
        config["handlers"]["file"]["formatter"] = "json"

    # Apply configuration
    logging.config.dictConfig(config)


def get_logger(name: str) -> logging.Logger:
    """
    Get logger instance.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(f"{_PACKAGE}.{name}")
