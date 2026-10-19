from __future__ import annotations

import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from bosun import __version__
from bosun.api.deps import ServicesDep
from bosun.api.schemas import BrowseOut, DirEntry, MetaOut, SandboxSupport
from bosun.config import APP_ROOT, running_in_container

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health(services: ServicesDep) -> dict[str, object]:
    return {"ok": True, "subscribers": services.bus.subscriber_count}


@router.get("/meta", response_model=MetaOut)
async def meta(services: ServicesDep) -> MetaOut:
    settings = await services.load_settings()
    return MetaOut(
        version=__version__,
        data_dir=str(services.cfg.data_dir),
        initial_workspace=str(services.cfg.default_initial_workspace()),
        app_root=str(APP_ROOT),
        backends=sorted(services.adapters),
        github_write_enabled=settings.github.write_enabled,
        sandbox=sandbox_support(),
    )


def sandbox_support() -> SandboxSupport:
    """Whether Claude Code's shell sandbox can start here (it refuses to run without it)."""
    if sys.platform == "darwin":
        return SandboxSupport(platform="macos", supported=True, missing=[])
    if sys.platform.startswith("linux"):
        missing = [tool for tool in ("bwrap", "socat") if shutil.which(tool) is None]
        return SandboxSupport(
            platform="linux",
            supported=not missing,
            missing=missing,
            container=running_in_container(),
        )
    return SandboxSupport(platform=sys.platform, supported=False, missing=[])


@router.get("/fs/browse", response_model=BrowseOut)
async def browse(path: str | None = Query(default=None)) -> BrowseOut:
    """List sub-directories to help pick a workspace. Directories only; nothing is read."""
    target = Path(path).expanduser() if path else Path.home()
    if not target.is_absolute():
        raise HTTPException(400, "Path must be absolute")
    target = target.resolve()
    if not target.is_dir():
        raise HTTPException(404, f"{target} is not a directory")
    entries: list[DirEntry] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise HTTPException(403, f"Cannot read {target}") from exc
    for child in children:
        if child.name.startswith(".") or not child.is_dir():
            continue
        try:
            entries.append(
                DirEntry(name=child.name, path=str(child), is_git=(child / ".git").exists())
            )
        except OSError:
            continue
        if len(entries) >= 500:
            break
    parent = str(target.parent) if target.parent != target else None
    return BrowseOut(path=str(target), parent=parent, entries=entries)
