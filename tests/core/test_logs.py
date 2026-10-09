"""The app's INFO logs reach stderr under uvicorn, which configures only its own loggers (#41)."""

import logging

from tillhand.core.logs import APP_LOGGER, configure_logging


def test_the_app_logs_at_info_through_one_handler() -> None:
    logger = configure_logging()
    configure_logging()  # idempotent

    assert logger is logging.getLogger(APP_LOGGER)
    assert logger.getEffectiveLevel() == logging.INFO
    assert len(logger.handlers) == 1
    assert logging.getLogger("tillhand.api.mcp.merchant_door").isEnabledFor(logging.INFO)
