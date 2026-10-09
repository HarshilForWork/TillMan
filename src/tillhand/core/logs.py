"""The app's own logs, on stderr at INFO, e.g. the Merchant door's "request with key …" line (#41).

uvicorn configures only its own loggers, so without this the app's INFO lines are silently dropped: Python's
fallback handler shows only warnings and worse. OpenTelemetry tracing replaces this later (#24).
"""

import logging

APP_LOGGER = "tillhand"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Idempotent: a second call adds no second handler."""
    logger = logging.getLogger(APP_LOGGER)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger
