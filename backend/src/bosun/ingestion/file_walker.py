"""Enumerate scannable files in a workspace.

* Git repositories: `git ls-files --cached --others --exclude-standard`, which
  applies every `.gitignore`, `.git/info/exclude` and global excludes exactly
  as git does.
* Plain directories: a walker that honours nested `.gitignore` files.

In both cases configurable exclusion globs, explicit excluded paths (such as
Bosun's own directory), a file-size limit, binary detection and the
symlink boundary (never leave the workspace root) are enforced.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

BINARY_SNIFF_BYTES = 8_192


@dataclass
class WalkOptions:
    exclude_globs: list[str]
    max_file_bytes: int
    max_files: int
    excluded_paths: list[Path] = field(default_factory=list)


@dataclass
class WalkStats:
    files_considered: int = 0
    skipped_excluded: int = 0
    skipped_large: int = 0
    skipped_binary: int = 0
    skipped_symlink: int = 0
    truncated: bool = False
    used_git: bool = False


def is_git_repo(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_files(root: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def _walk_plain(root: Path) -> Iterator[str]:
    """Walk without following symlinked directories, honouring nested .gitignore files."""
    specs: dict[Path, pathspec.PathSpec] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        gitignore = current / ".gitignore"
        if gitignore.is_file():
            with contextlib.suppress(OSError):
                specs[current] = pathspec.GitIgnoreSpec.from_lines(
                    gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
                )

        def ignored(path: Path, is_dir: bool) -> bool:
            for base, spec in specs.items():
                try:
                    rel = path.relative_to(base).as_posix()
                except ValueError:
                    continue
                if spec.match_file(rel + ("/" if is_dir else "")):
                    return True
            return False

        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not ignored(current / d, True))
        for name in sorted(filenames):
            full = current / name
            if not ignored(full, False):
                yield full.relative_to(root).as_posix()


def iter_workspace_files(root: Path, options: WalkOptions, stats: WalkStats) -> Iterator[Path]:
    root = root.resolve()
    exclude_spec = pathspec.GitIgnoreSpec.from_lines(options.exclude_globs)
    excluded = [p.resolve() for p in options.excluded_paths]

    rel_paths: list[str] | None = None
    if is_git_repo(root):
        rel_paths = _git_files(root)
        stats.used_git = rel_paths is not None
    candidates: Iterator[str] | list[str] = (
        rel_paths if rel_paths is not None else _walk_plain(root)
    )

    yielded = 0
    for rel in candidates:
        stats.files_considered += 1
        if exclude_spec.match_file(rel):
            stats.skipped_excluded += 1
            continue
        full = root / rel
        try:
            if full.is_symlink():
                target = full.resolve()
                if not target.is_relative_to(root):
                    stats.skipped_symlink += 1
                    continue
                full_resolved = target
            else:
                full_resolved = full
            if any(full_resolved.is_relative_to(ex) or full.is_relative_to(ex) for ex in excluded):
                stats.skipped_excluded += 1
                continue
            st = full_resolved.stat()
        except OSError:
            continue
        if not full_resolved.is_file():
            continue
        if st.st_size > options.max_file_bytes:
            stats.skipped_large += 1
            continue
        try:
            with full_resolved.open("rb") as fh:
                if b"\0" in fh.read(BINARY_SNIFF_BYTES):
                    stats.skipped_binary += 1
                    continue
        except OSError:
            continue
        yield full
        yielded += 1
        if yielded >= options.max_files:
            stats.truncated = True
            return
