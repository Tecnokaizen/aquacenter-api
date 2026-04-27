from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI

from app.core.config import ensure_runtime_dirs, settings
from app.routers.extract import router as extract_router
from app.routers.health import router as health_router


def _configure_logging() -> None:
    ensure_runtime_dirs()
    log_file = Path(settings.logs_dir) / "api.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


_configure_logging()

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(health_router)
app.include_router(extract_router)

