"""
Logging configuration for Polymarket client.

Provides structured logging for production use.
"""

import logging
import logging.config
import sys
from typing import Optional


DEFAULT_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
        },
        "detailed": {
            "format": (
                "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d "
                "- %(message)s (%(funcName)s)"
            ),
            "datefmt": "%Y-%m-%d %H:%M:%S"
        },
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "standard",
            "stream": "ext://sys.stdout"
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "filename": "polymarket_client.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "ERROR",
            "formatter": "detailed",
            "filename": "polymarket_errors.log",
            "maxBytes": 10485760,
            "backupCount": 5
        }
    },
    "loggers": {
        "shared.polymarket": {
            "level": "INFO",
            "handlers": ["console", "file", "error_file"],
            "propagate": False
        }
    },
    "root": {
        "level": "WARNING",
        "handlers": ["console"]
    }
}


def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    json_format: bool = False
) -> None:
    """
    Setup logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file path
        json_format: Use JSON formatting
    """
    config = DEFAULT_LOGGING_CONFIG.copy()

    # Override level
    if level:
        config["loggers"]["shared.polymarket"]["level"] = level.upper()

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
    return logging.getLogger(f"shared.polymarket.{name}")
