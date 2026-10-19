from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from bosun.api.deps import ServicesDep
from bosun.demo.seed import remove_demo, seed_demo

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("")
async def seed(services: ServicesDep, launch_runs: bool = True) -> dict[str, Any]:
    return await seed_demo(services, launch_runs=launch_runs)


@router.delete("")
async def remove(services: ServicesDep) -> dict[str, int]:
    return await remove_demo(services)
