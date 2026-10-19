"""Provider selection, authentication status and the billing guard.

Bosun never switches providers on its own. The provider is an
explicit setting; every session runs with an environment that pins it, and the
Claude Code init frame (`apiKeySource`) plus per-model usage (`provider`) are
verified against the expectation — a mismatch aborts the session immediately.

Subscription mode removes inherited `ANTHROPIC_API_KEY` / Vertex / Bedrock
variables from the agent's environment so a stray shell variable cannot turn a
subscription-backed run into a billed API run.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import claude_agent_sdk

from bosun.agents.contracts import ProviderRuntime
from bosun.config import Settings
from bosun.domain.enums import Provider
from bosun.settings_store import ProviderSettings

# Variables that select a billed provider or credential in Claude Code.
BILLING_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
)

# Marks Claude Code processes started by Bosun (also used to find orphans).
CLIENT_APP_ENV = "CLAUDE_AGENT_SDK_CLIENT_APP"

POLICY_NOTE = (
    "Subscription mode reuses your own local Claude Code login and is intended for personal, "
    "local use. Anthropic does not permit third-party products to offer claude.ai login to "
    "other users; use an API key or Vertex AI for anything you share or host."
)


class ProviderConfigError(Exception):
    pass


def claude_cli_path(cfg: Settings) -> str | None:
    if cfg.claude_cli_path:
        return str(cfg.claude_cli_path)
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("claude")


def _blank_inherited_billing_env() -> dict[str, str]:
    return {name: "" for name in BILLING_ENV_VARS if os.environ.get(name)}


def _app_environment_overrides() -> dict[str, str]:
    """Keep Bosun's own environment out of agent shells.

    Bosun's settings (tokens and keys included, as passed to a container) are
    blanked. Started with `uv run`, the backend's virtualenv is first on PATH;
    agents would otherwise run the user's tests with Bosun's interpreter.
    """
    overrides = {name: "" for name in os.environ if name.startswith("BOSUN_")}
    if sys.prefix == sys.base_prefix:
        return overrides
    venv_bin = str(Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin"))
    overrides["PATH"] = os.pathsep.join(
        entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry != venv_bin
    )
    if os.environ.get("VIRTUAL_ENV"):
        overrides["VIRTUAL_ENV"] = ""
    return overrides


def build_provider_runtime(prefs: ProviderSettings, cfg: Settings) -> ProviderRuntime:
    blank = _blank_inherited_billing_env()
    env_common = {**_app_environment_overrides(), CLIENT_APP_ENV: "bosun/0.1"}
    if prefs.provider == Provider.SUBSCRIPTION:
        return ProviderRuntime(
            provider=Provider.SUBSCRIPTION,
            label="Claude subscription",
            env={**blank, **env_common},
            expected_api_key_sources=frozenset({"none"}),
            expected_usage_providers=frozenset({"firstParty"}),
        )
    if prefs.provider == Provider.API_KEY:
        key = cfg.anthropic_api_key
        if not key:
            raise ProviderConfigError(
                "API key provider selected but BOSUN_ANTHROPIC_API_KEY is not set. "
                "Add it to Bosun's .env and restart."
            )
        return ProviderRuntime(
            provider=Provider.API_KEY,
            label="Anthropic API key",
            env={**blank, **env_common, "ANTHROPIC_API_KEY": key},
            expected_api_key_sources=frozenset({"ANTHROPIC_API_KEY"}),
            expected_usage_providers=frozenset({"firstParty"}),
        )
    project = prefs.vertex_project_id or cfg.vertex_project_id
    region = prefs.vertex_region or cfg.vertex_region or "global"
    if not project:
        raise ProviderConfigError(
            "Vertex AI provider selected but no GCP project is configured "
            "(Settings → Provider, or BOSUN_VERTEX_PROJECT_ID)."
        )
    return ProviderRuntime(
        provider=Provider.VERTEX,
        label="Google Vertex AI",
        env={
            **blank,
            **env_common,
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLOUD_ML_REGION": region,
            "ANTHROPIC_VERTEX_PROJECT_ID": project,
        },
        expected_api_key_sources=frozenset({"none"}),
        expected_usage_providers=frozenset({"vertex", "anthropicGoogleCloud"}),
    )


@dataclass
class ProviderStatus:
    provider: Provider
    ready: bool
    summary: str
    details: dict[str, Any]
    checked_at: float


_status_cache: dict[str, ProviderStatus] = {}
_STATUS_TTL_S = 60.0


async def _run_cli_json(
    args: list[str], env: dict[str, str], timeout: float = 20
) -> dict[str, Any] | None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **env},
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return None
    try:
        return json.loads(stdout.decode() or "null")
    except json.JSONDecodeError:
        return None


async def check_provider_status(
    prefs: ProviderSettings, cfg: Settings, *, force: bool = False
) -> ProviderStatus:
    cache_key = f"{prefs.provider}:{prefs.vertex_project_id}:{prefs.vertex_region}"
    cached = _status_cache.get(cache_key)
    if cached and not force and time.monotonic() - cached.checked_at < _STATUS_TTL_S:
        return cached

    status = await _check(prefs, cfg)
    _status_cache[cache_key] = status
    return status


async def _check(prefs: ProviderSettings, cfg: Settings) -> ProviderStatus:
    now = time.monotonic()
    cli = claude_cli_path(cfg)
    if cli is None:
        return ProviderStatus(
            prefs.provider, False, "Claude Code CLI not found", {"cli_path": None}, now
        )

    if prefs.provider == Provider.API_KEY:
        ready = bool(cfg.anthropic_api_key)
        return ProviderStatus(
            Provider.API_KEY,
            ready,
            "API key configured (usage is billed to your Anthropic account)"
            if ready
            else "BOSUN_ANTHROPIC_API_KEY is not set",
            {"cli_path": cli, "billing": "per-token API billing"},
            now,
        )

    if prefs.provider == Provider.VERTEX:
        project = prefs.vertex_project_id or cfg.vertex_project_id
        adc = Path(
            os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            or Path.home() / ".config/gcloud/application_default_credentials.json"
        )
        ready = bool(project) and adc.exists()
        problems = []
        if not project:
            problems.append("no GCP project configured")
        if not adc.exists():
            problems.append(
                "Application Default Credentials not found (run `gcloud auth application-default login`)"
            )
        return ProviderStatus(
            Provider.VERTEX,
            ready,
            "Vertex AI configured" if ready else "; ".join(problems),
            {
                "cli_path": cli,
                "project": project,
                "region": prefs.vertex_region or cfg.vertex_region or "global",
                "credentials": str(adc) if adc.exists() else None,
                "billing": "billed to your Google Cloud project",
            },
            now,
        )

    runtime_env = _blank_inherited_billing_env()
    data = await _run_cli_json([cli, "auth", "status"], runtime_env)
    if not data:
        return ProviderStatus(
            Provider.SUBSCRIPTION,
            False,
            "Could not read Claude Code authentication status",
            {"cli_path": cli},
            now,
        )
    logged_in = bool(data.get("loggedIn"))
    method = data.get("authMethod")
    ready = (
        logged_in
        and data.get("apiProvider", "firstParty") == "firstParty"
        and method
        not in (
            "api_key",
            "apiKey",
            "console",
        )
    )
    summary = (
        f"Signed in with Claude ({data.get('subscriptionType') or 'subscription'})"
        if ready
        else "Not signed in to a Claude subscription — run `claude auth login`"
    )
    return ProviderStatus(
        Provider.SUBSCRIPTION,
        ready,
        summary,
        {
            "cli_path": cli,
            "auth_method": method,
            "subscription_type": data.get("subscriptionType"),
            "email": data.get("email"),
            "org_name": data.get("orgName"),
            "api_provider": data.get("apiProvider"),
            "inherited_billing_env_blanked": sorted(runtime_env),
            "billing": "uses your Claude subscription limits; no per-request charges are reported",
        },
        now,
    )
