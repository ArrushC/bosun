from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.schemas import ApprovalOut, ApprovalResolve
from bosun.api.serialize import approvals_out
from bosun.db.models import ApprovalRequest

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    session: SessionDep, status: str = "pending", run_id: str | None = None
) -> list[ApprovalOut]:
    stmt = select(ApprovalRequest)
    if status != "all":
        stmt = stmt.where(ApprovalRequest.status == status)
    if run_id:
        stmt = stmt.where(ApprovalRequest.run_id == run_id)
    rows = (
        await session.scalars(stmt.order_by(ApprovalRequest.created_at.desc()).limit(500))
    ).all()
    return await approvals_out(session, rows)


@router.post("/{approval_id}/resolve", response_model=ApprovalOut)
async def resolve(
    approval_id: str, body: ApprovalResolve, session: SessionDep, services: ServicesDep
) -> ApprovalOut:
    try:
        row = await services.approvals.resolve(
            approval_id,
            approve=body.decision == "approve",
            message=body.message,
            remember=body.remember,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return (await approvals_out(session, [row]))[0]
