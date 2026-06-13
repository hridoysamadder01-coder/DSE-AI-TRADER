import sys

from loguru import logger

from .config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | <cyan>{name}</cyan> | {message}",
    )
    logger.add(
        settings.logs_dir / "app.log",
        level=settings.log_level,
        rotation="20 MB",
        retention="14 days",
        compression="zip",
        enqueue=True,
    )
