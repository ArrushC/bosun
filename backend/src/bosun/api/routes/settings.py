from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from bosun.agents.providers import POLICY_NOTE, check_provider_status
from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.schemas import ProviderStatusOut, RoleIn, RoleOut, RoleUpdate
from bosun.db.models import AgentRole, ExecutionEvent
from bosun.domain.enums import Provider
from bosun.settings_store import (
    AppSettings,
    ProviderSettings,
    load_settings,
    save_settings,
)

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=AppSettings)
async def get_settings_route(session: SessionDep) -> AppSettings:
    return await load_settings(session)


@router.put("/settings", response_model=AppSettings)
async def put_settings(
    body: AppSettings, session: SessionDep, services: ServicesDep
) -> AppSettings:
    saved = await save_settings(session, body)
    await services.limiter.resize(saved.execution.max_concurrent_sessions)
    services.bus.notify("settings", None)
    return saved


async def _latest_usage(session: SessionDep) -> dict[str, Any]:
    """Subscription usage information reported by Claude Code (rate-limit windows)."""
    rows = (
        await session.scalars(
            select(ExecutionEvent)
            .where(ExecutionEvent.type == "rate_limit")
            .order_by(ExecutionEvent.seq.desc())
            .limit(20)
        )
    ).all()
    windows: dict[str, Any] = {}
    for row in rows:
        kind = row.data.get("type") or "unknown"
        if kind not in windows:
            windows[kind] = {**row.data, "observed_at": row.ts.isoformat()}
    return {"rate_limits": list(windows.values())}


@router.get("/providers/status", response_model=ProviderStatusOut)
async def provider_status(
    session: SessionDep, services: ServicesDep, force: bool = False
) -> ProviderStatusOut:
    settings = await load_settings(session)
    status = await check_provider_status(settings.provider, services.cfg, force=force)
    return ProviderStatusOut(
        provider=status.provider,
        ready=status.ready,
        summary=status.summary,
        details=status.details,
        policy_note=POLICY_NOTE,
        usage=await _latest_usage(session),
    )


@router.get("/providers", response_model=list[ProviderStatusOut])
async def providers(
    session: SessionDep, services: ServicesDep, force: bool = False
) -> list[ProviderStatusOut]:
    settings = await load_settings(session)
    out = []
    for provider in Provider:
        prefs = ProviderSettings(**{**settings.provider.model_dump(), "provider": provider})
        status = await check_provider_status(prefs, services.cfg, force=force)
        out.append(
            ProviderStatusOut(
                provider=provider,
                ready=status.ready,
                summary=status.summary,
                details=status.details,
                policy_note=POLICY_NOTE,
            )
        )
    return out


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(session: SessionDep) -> list[RoleOut]:
    rows = (
        await session.scalars(
            select(AgentRole).order_by(AgentRole.is_coordinator.desc(), AgentRole.created_at)
        )
    ).all()
    return [RoleOut.model_validate(r) for r in rows]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleIn, session: SessionDep, services: ServicesDep) -> RoleOut:
    if await session.scalar(select(AgentRole).where(AgentRole.key == body.key)):
        raise HTTPException(409, f"A role with key '{body.key}' already exists")
    role = AgentRole(**body.model_dump(), builtin=False, is_coordinator=False)
    session.add(role)
    await session.commit()
    services.bus.notify("roles", role.id)
    return RoleOut.model_validate(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: str, body: RoleUpdate, session: SessionDep, services: ServicesDep
) -> RoleOut:
    role = await session.get(AgentRole, role_id)
    if role is None:
        raise HTTPException(404, "Role not found")
    patch = body.model_dump(exclude_unset=True)
    if role.is_coordinator and patch.get("permission_profile") not in (None, "read_only"):
        raise HTTPException(400, "The coordinator must stay read-only")
    for key, value in patch.items():
        setattr(role, key, value)
    await session.commit()
    services.bus.notify("roles", role_id)
    return RoleOut.model_validate(role)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: str, session: SessionDep, services: ServicesDep) -> None:
    role = await session.get(AgentRole, role_id)
    if role is None:
        raise HTTPException(404, "Role not found")
    if role.builtin:
        raise HTTPException(400, "Built-in roles can be disabled but not deleted")
    await session.delete(role)
    await session.commit()
    services.bus.notify("roles", role_id)
