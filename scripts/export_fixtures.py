"""Record real event logs from the demo backend as frontend test fixtures.

    uv run --directory backend python ../scripts/export_fixtures.py [OUT_DIR] [NAME ...]

With names, only those fixtures are recorded.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tests.conftest import add_task, add_workspace, make_repo, make_settings  # noqa: E402

from bosun.agents.fake import FakeAgentAdapter  # noqa: E402
from bosun.agents.roles import seed_roles  # noqa: E402
from bosun.db.migrate import upgrade_to_head  # noqa: E402
from bosun.db.models import Workflow  # noqa: E402
from bosun.db.session import Database  # noqa: E402
from bosun.domain.enums import ExecutionMode  # noqa: E402
from bosun.orchestration.config import RunRequest  # noqa: E402
from bosun.services import build_services  # noqa: E402
from bosun.workflows.spec import WorkflowSpec, WorkflowStep  # noqa: E402


async def main(out_dir: Path, only: set[str]) -> None:
    tmp = Path(tempfile.mkdtemp())
    settings = make_settings(tmp)
    settings.data_dir.mkdir(parents=True)
    await asyncio.to_thread(upgrade_to_head, settings.resolved_database_url)
    db = Database(settings.resolved_database_url)
    services = build_services(settings, db, {"demo": FakeAgentAdapter(step_delay=0)})
    async with db.session() as session:
        await seed_roles(session)
    repo = make_repo(tmp / "repo")
    ws = await add_workspace(services, repo)
    fix = await _workflow(services, "Fix with review", FIX_WITH_REVIEW)
    branches = await _workflow(services, "Two branches", TWO_BRANCHES)
    for name, request in {
        "team-run": RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False),
        "single-run": RunRequest(backend="demo", demo_scenario={"needs_approval": True}),
        "workflow-run": RunRequest(backend="demo", workflow_id=fix),
        # The researcher step fails for good; the step after it never runs.
        "workflow-failed": RunRequest(
            backend="demo", workflow_id=branches, demo_scenario={"fail": "process_crash", "fail_role": "researcher"}
        ),
    }.items():
        if only and name not in only:
            continue
        task = await add_task(services, ws, title=f"Fixture {name}")
        run = await services.runs.start(task.id, request)
        while services.runs.context(run.id) is not None:
            for approval in await _pending(services, run.id):
                await services.approvals.resolve(approval, approve=True, message=None, remember=False)
            await asyncio.sleep(0.02)
        events = await services.bus.history(run_id=run.id, limit=100_000)
        (out_dir / f"{name}.json").write_text(json.dumps(events, indent=1, default=str))
        print(name, len(events), "events")
    await db.dispose()


def _step(key: str, title: str, agent: str, *after: str, **fields: object) -> WorkflowStep:
    return WorkflowStep(key=key, title=title, agent=agent, instructions=f"{title}.", after=list(after), **fields)  # type: ignore[arg-type]


FIX_WITH_REVIEW = [
    _step("research", "Research", "researcher"),
    _step("implement", "Implement", "implementer", "research", changes_code=True),
    _step("test", "Add tests", "tester", "implement", changes_code=True),
    _step("review", "Review", "reviewer", "implement"),
    _step("final", "Final check", "reviewer", "test", "review"),
]
TWO_BRANCHES = [
    _step("left", "Check the API", "researcher", retries=0),
    _step("left2", "Summarise API findings", "reviewer", "left"),
    _step("right", "Check the docs", "reviewer"),
    _step("right2", "Summarise doc findings", "reviewer", "right"),
]


async def _workflow(services, name: str, steps: list[WorkflowStep]) -> str:  # noqa: ANN001
    async with services.db.session() as session:
        row = Workflow(name=name, spec=WorkflowSpec(steps=steps).model_dump(mode="json"))
        session.add(row)
        await session.commit()
        return row.id


async def _pending(services, run_id):  # noqa: ANN001, ANN202
    from sqlalchemy import select

    from bosun.db.models import ApprovalRequest

    async with services.db.session() as session:
        rows = (
            await session.scalars(
                select(ApprovalRequest).where(ApprovalRequest.run_id == run_id, ApprovalRequest.status == "pending")
            )
        ).all()
    return [r.id for r in rows]


DEFAULT_OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "test" / "fixtures"
asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT, set(sys.argv[2:])))
