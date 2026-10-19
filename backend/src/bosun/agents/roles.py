"""Built-in agent roles, seeded into the database and editable from Settings."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.db.models import AgentRole
from bosun.domain.enums import PermissionProfile

REVIEW_GIT_COMMANDS = (
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git status:*)",
)


@dataclass(frozen=True)
class RoleSeed:
    key: str
    name: str
    description: str
    system_prompt: str
    permission_profile: PermissionProfile
    color: str
    is_coordinator: bool = False
    max_turns: int | None = None
    extra_allowed_tools: tuple[str, ...] = ()


BUILTIN_ROLES: list[RoleSeed] = [
    RoleSeed(
        key="coordinator",
        name="Coordinator",
        description="Understands the task, decomposes it into subtasks, assigns roles and dependencies, and writes the final summary.",
        system_prompt=(
            "You lead a small team of software agents. Understand the task and the codebase well enough to "
            "plan, but do not implement anything yourself. Decompose the work into the smallest set of "
            "well-scoped subtasks, choose the right role for each, and make dependencies explicit so that "
            "independent work can run in parallel and conflicting edits never do."
        ),
        permission_profile=PermissionProfile.READ_ONLY,
        color="amber",
        is_coordinator=True,
        max_turns=30,
    ),
    RoleSeed(
        key="researcher",
        name="Researcher",
        description="Explores the existing code, history and documentation; reports findings, relevant files and risks. Never edits.",
        system_prompt=(
            "You investigate. Find where the relevant behaviour lives, how it is tested, related history and "
            "constraints. Report concrete file paths, functions and risks that the implementer needs. "
            "Do not modify files."
        ),
        permission_profile=PermissionProfile.READ_ONLY,
        color="sky",
        max_turns=40,
    ),
    RoleSeed(
        key="implementer",
        name="Implementer",
        description="Makes the code changes required by the task, following existing conventions, and verifies them.",
        system_prompt=(
            "You implement. Make the smallest correct change that satisfies the task, matching the "
            "surrounding code's conventions. Run the relevant tests or checks when possible and report "
            "exactly what you verified."
        ),
        permission_profile=PermissionProfile.EDIT_AND_TEST,
        color="violet",
    ),
    RoleSeed(
        key="tester",
        name="Tester",
        description="Writes and runs tests that cover the change, reports failures with evidence.",
        system_prompt=(
            "You test. Write focused tests that cover the new or changed behaviour, including edge cases, "
            "run them, and report results with the exact commands and output. Fix tests, not product "
            "code, unless the task says otherwise; report product bugs you find."
        ),
        permission_profile=PermissionProfile.EDIT_AND_TEST,
        color="emerald",
    ),
    RoleSeed(
        key="reviewer",
        name="Reviewer",
        description="Reviews the change for correctness, maintainability and risk. Read-only.",
        system_prompt=(
            "You review. Read the change (use `git diff` and `git log` in your working directory) and the "
            "surrounding code. Report correctness bugs first, then maintainability issues and risks, each "
            "with file and line references. Do not modify files."
        ),
        permission_profile=PermissionProfile.READ_ONLY,
        color="rose",
        max_turns=40,
        extra_allowed_tools=REVIEW_GIT_COMMANDS,
    ),
]


async def seed_roles(session: AsyncSession) -> None:
    existing = set((await session.scalars(select(AgentRole.key))).all())
    for seed in BUILTIN_ROLES:
        if seed.key in existing:
            continue
        session.add(
            AgentRole(
                key=seed.key,
                name=seed.name,
                description=seed.description,
                system_prompt=seed.system_prompt,
                permission_profile=seed.permission_profile,
                color=seed.color,
                is_coordinator=seed.is_coordinator,
                max_turns=seed.max_turns,
                extra_allowed_tools=list(seed.extra_allowed_tools),
                builtin=True,
                enabled=True,
            )
        )
    await session.commit()
