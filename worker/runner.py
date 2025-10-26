from __future__ import annotations

import json
import os
import traceback
from typing import Any, Dict

from settings import load_settings
from core.engine.artifacts import ensure_dir
from core.logging_config import configure_logging, get_logger
from worker.backtest_core import execute_backtest, save_artifacts

logger = get_logger(__name__)


def _status_path(root: str, run_id: str) -> str:
    return os.path.join(root, run_id, "status.json")


def _update_status(root: str, run_id: str, status: str, message: str = "") -> None:
    ensure_dir(os.path.join(root, run_id))
    path = _status_path(root, run_id)
    try:
        payload: Dict[str, Any] = {"run_id": run_id}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        payload.update({"status": status, "message": message})
    except Exception:
        payload = {"run_id": run_id, "status": status, "message": message}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def run_backtest(run_id: str, config: Dict[str, Any]) -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_file, settings.log_console)
    root = settings.artifacts_root
    ensure_dir(os.path.join(root, run_id))
    _update_status(root, run_id, "RUNNING", "started")
    logger.info("Backtest started", extra={"run_id": run_id})

    try:
        result = execute_backtest(config, settings)
        save_artifacts(run_id, result, root)
        _update_status(root, run_id, "COMPLETED", "ok")
        logger.info("Backtest completed", extra={"run_id": run_id, "entries": result.get("summary", {}).get("entries")})
    except Exception as exc:
        _update_status(root, run_id, "FAILED", str(exc))
        error_path = os.path.join(root, run_id, "error.txt")
        with open(error_path, "w", encoding="utf-8") as f:
            f.write(str(exc) + "\n" + traceback.format_exc())
        logger.exception("Backtest failed", extra={"run_id": run_id})
        raise
