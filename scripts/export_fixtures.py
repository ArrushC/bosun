"""Record real event logs from the demo backend as frontend test fixtures."""

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
from bosun.db.session import Database  # noqa: E402
from bosun.domain.enums import ExecutionMode  # noqa: E402
from bosun.orchestration.config import RunRequest  # noqa: E402
from bosun.services import build_services  # noqa: E402


async def main(out_dir: Path) -> None:
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
    for name, request in {
        "team-run": RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False),
        "single-run": RunRequest(backend="demo", demo_scenario={"needs_approval": True}),
    }.items():
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
asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT))
