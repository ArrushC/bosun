"""Application service container wired up at startup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bosun.agents.contracts import AgentAdapter
from bosun.config import Settings
from bosun.db.session import Database
from bosun.events.bus import EventBus
from bosun.orchestration.git import WorktreeManager
from bosun.settings_store import AppSettings, load_settings

if TYPE_CHECKING:
    from bosun.ingestion.service import IngestionService
    from bosun.orchestration.manager import RunManager
    from bosun.orchestration.runtime import ApprovalBroker, Limiter, RunStore


@dataclass
class Services:
    cfg: Settings
    db: Database
    bus: EventBus
    worktrees: WorktreeManager
    adapters: dict[str, AgentAdapter]
    store: RunStore = field(init=False)
    approvals: ApprovalBroker = field(init=False)
    limiter: Limiter = field(init=False)
    runs: RunManager = field(init=False)
    ingestion: IngestionService = field(init=False)

    async def load_settings(self) -> AppSettings:
        async with self.db.session() as session:
            return await load_settings(session)


def build_services(cfg: Settings, db: Database, adapters: dict[str, AgentAdapter]) -> Services:
    from bosun.ingestion.service import IngestionService
    from bosun.orchestration.manager import RunManager
    from bosun.orchestration.runtime import ApprovalBroker, Limiter, RunStore

    bus = EventBus(db)
    services = Services(
        cfg=cfg, db=db, bus=bus, worktrees=WorktreeManager(cfg.worktrees_dir), adapters=adapters
    )
    services.store = RunStore(db, bus)
    services.approvals = ApprovalBroker(services)
    services.limiter = Limiter(3)
    services.runs = RunManager(services)
    services.ingestion = IngestionService(services)
    return services
