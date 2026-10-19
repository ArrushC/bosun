"""Run configuration snapshot.

Everything that determines how a run behaves is resolved once, when the run is
created, and stored on the run. The launch dialog shows this exact snapshot as
the execution plan and scope, and later changes to Settings do not alter runs
that already exist.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from bosun.agents.permissions import tool_policy
from bosun.domain.enums import ExecutionBackend, ExecutionMode, Provider
from bosun.settings_store import ExecutionSettings


class RoleConfig(BaseModel):
    key: str
    name: str
    description: str = ""
    system_prompt: str = ""
    permission_profile: str
    model: str | None = None
    effort: str | None = None
    max_turns: int | None = None
    extra_allowed_tools: list[str] = Field(default_factory=list)
    is_coordinator: bool = False
    color: str = "violet"


class RunRequest(BaseModel):
    mode: ExecutionMode | None = None
    role: str | None = None
    team_roles: list[str] | None = None
    backend: ExecutionBackend | None = None
    model: str | None = None
    effort: str | None = None
    max_turns: int | None = Field(default=None, ge=1, le=500)
    timeout_minutes: int | None = Field(default=None, ge=1, le=600)
    require_plan_approval: bool | None = None
    allow_subagents: bool | None = None
    max_team_size: int | None = Field(default=None, ge=1, le=8)
    instructions: str | None = None
    demo_scenario: dict[str, Any] = Field(default_factory=dict)


class RunConfig(BaseModel):
    mode: ExecutionMode
    backend: ExecutionBackend
    provider: Provider
    model: str | None = None
    effort: str | None = None
    single_role: str = "implementer"
    team_roles: list[str] = Field(default_factory=list)
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    max_turns: int = 60
    timeout_minutes: int = 45
    require_plan_approval: bool = True
    max_team_size: int = 4
    max_parallel: int = 2
    subtask_max_attempts: int = 2
    allow_subagents: bool = False
    allow_web_tools: bool = False
    auto_allow_bash: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    commit_agent_changes: bool = True
    sandbox_bash: bool = False
    trusted_workspace: bool = False
    instructions: str | None = None
    remembered_approvals: list[dict[str, str]] = Field(default_factory=list)
    demo_scenario: dict[str, Any] = Field(default_factory=dict)

    def execution_settings(self) -> ExecutionSettings:
        return ExecutionSettings(
            auto_allow_bash=self.auto_allow_bash,
            test_commands=self.test_commands,
            allow_web_tools=self.allow_web_tools,
        )

    def coordinator(self) -> RoleConfig:
        for role in self.roles.values():
            if role.is_coordinator:
                return role
        return RoleConfig(
            key="coordinator",
            name="Coordinator",
            permission_profile="read_only",
            is_coordinator=True,
        )

    def scope(self) -> dict[str, Any]:
        """Human-readable permissions per role for the launch dialog."""
        execution = self.execution_settings()
        relevant = (
            [self.single_role]
            if self.mode == ExecutionMode.SINGLE
            else [self.coordinator().key, *self.team_roles]
        )
        roles = {}
        for key in relevant:
            role = self.roles.get(key)
            if role is None:
                continue
            policy = tool_policy(
                role.permission_profile,
                execution,
                extra_allowed=role.extra_allowed_tools,
                allow_subagents=self.allow_subagents and not role.is_coordinator,
            )
            roles[key] = {
                "name": role.name,
                "profile": role.permission_profile,
                "description": policy.description,
                "tools": policy.tools,
                "auto_allowed": policy.auto_allow,
                "disallowed": policy.disallowed,
                "model": role.model or self.model,
            }
        return roles
