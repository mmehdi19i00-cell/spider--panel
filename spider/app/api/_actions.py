"""Shared action helpers used by routers (regenerate config + reload xray)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.xray.builder import write_config
from app.xray.process import manager
from app.xray.validator import validate_config


async def regenerate_and_reload(db: AsyncSession, *, reload: bool = True) -> dict:
    """Write config.json from DB, validate, and (optionally) reload xray.

    Returns a status dict suitable for API responses.
    """
    path = await write_config(db)
    ok, errors = validate_config(await _load_json(path))
    result = {"config_path": path, "valid": ok, "errors": errors}
    if not ok:
        log.warning(f"Generated config invalid: {errors}")
        return result
    if reload:
        started = await manager.restart()
        result["xray_restarted"] = started
    return result


async def _load_json(path: str) -> dict:
    import json

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
