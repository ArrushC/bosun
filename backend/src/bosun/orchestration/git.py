"""Git worktree isolation for agent runs.

Layout (under `<data_dir>/worktrees/`):

    <run_id>/                 run worktree on branch  mc/<task>-<run>
    <run_id>__<subtask_key>/  child worktree for a write-capable team subtask,
                              on branch mc/<task>-<run>--<key>, merged back into
                              the run branch when the subtask finishes

The user's own checkout is never modified during a run. Integration into the
user's branch is a separate, explicit, confirmed action.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

AGENT_AUTHOR = ("Bosun", "agents@bosun.local")
MAX_DIFF_BYTES = 2_000_000
# Never committed on behalf of agents, even when the repository does not ignore them.
COMMIT_EXCLUDES = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.tox/**",
    "**/node_modules/**",
    "**/.DS_Store",
    "**/*.swp",
    "**/.coverage",
)


class GitError(Exception):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(f"{message}: {stderr.strip()}" if stderr.strip() else message)
        self.stderr = stderr


@dataclass
class GitResult:
    code: int
    stdout: str
    stderr: str


async def git(cwd: Path | str, *args: str, check: bool = True, timeout: float = 120) -> GitResult:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": AGENT_AUTHOR[0],
        "GIT_AUTHOR_EMAIL": AGENT_AUTHOR[1],
        "GIT_COMMITTER_NAME": AGENT_AUTHOR[0],
        "GIT_COMMITTER_EMAIL": AGENT_AUTHOR[1],
    }
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise GitError(f"git {' '.join(args)} timed out") from None
    result = GitResult(
        proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    )
    if check and result.code != 0:
        raise GitError(f"git {' '.join(args[:3])} failed", result.stderr or result.stdout)
    return result


def branch_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "task"


@dataclass
class RunWorkspace:
    path: Path
    branch: str | None
    base_branch: str | None
    base_commit: str | None
    isolation: str  # "worktree" | "in_place"
    dirty_source: bool = False
    base_label: str | None = None
    notice: str | None = None


class WorktreeManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def is_repo(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        result = await git(path, "rev-parse", "--is-inside-work-tree", check=False)
        return result.code == 0 and result.stdout.strip() == "true"

    async def head(self, path: Path) -> str:
        return (await git(path, "rev-parse", "HEAD")).stdout.strip()

    async def current_branch(self, path: Path) -> str | None:
        result = await git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
        name = result.stdout.strip()
        return None if result.code != 0 or name == "HEAD" else name

    async def has_commits(self, path: Path) -> bool:
        return (await git(path, "rev-parse", "--verify", "HEAD", check=False)).code == 0

    async def is_dirty(self, path: Path, *, untracked: bool = False) -> bool:
        mode = "--untracked-files=normal" if untracked else "--untracked-files=no"
        result = await git(path, "status", "--porcelain", mode, check=False)
        return bool(result.stdout.strip())

    async def fetch_pull_request(self, repo_root: Path, repo: str, number: int) -> str | None:
        """Fetch a GitHub pull request head (read-only) when `origin` is that repository."""
        remote = await git(repo_root, "remote", "get-url", "origin", check=False)
        url = remote.stdout.strip().lower().removesuffix(".git")
        if remote.code != 0 or not url.endswith(repo.lower()):
            return None
        ref = f"refs/bosun/pr-{number}"
        result = await git(
            repo_root,
            "fetch",
            "--quiet",
            "origin",
            f"+pull/{number}/head:{ref}",
            check=False,
            timeout=180,
        )
        if result.code != 0:
            return None
        return (await git(repo_root, "rev-parse", ref)).stdout.strip()

    async def prepare_run(
        self,
        workspace: Path,
        run_id: str,
        task_key: str,
        title: str,
        pull_request: tuple[str, int] | None = None,
    ) -> RunWorkspace:
        if not await self.is_repo(workspace) or not await self.has_commits(workspace):
            return RunWorkspace(workspace, None, None, None, "in_place")
        repo_root = Path((await git(workspace, "rev-parse", "--show-toplevel")).stdout.strip())
        # When the workspace is a subdirectory of a repository, agents work in the
        # same subdirectory of the worktree.
        subdir = workspace.resolve().relative_to(repo_root.resolve())
        base_branch = await self.current_branch(repo_root)
        base_commit = await self.head(repo_root)
        dirty = await self.is_dirty(repo_root)
        base_label, notice = base_branch, None
        if pull_request is not None:
            # Pull-request tasks (review, testing, improvements) start from the PR head.
            repo, number = pull_request
            head = await self.fetch_pull_request(repo_root, repo, number)
            if head:
                base_commit, base_label = head, f"{repo}#{number} (pull request head)"
            else:
                notice = (
                    f"Could not fetch pull request {repo}#{number} from origin; the agent starts from "
                    f"{base_branch or 'HEAD'} instead."
                )
        branch = f"bosun/{task_key.lower()}-{branch_slug(title)[:20]}-{run_id[-6:]}"
        path = self.root / run_id
        self.root.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            await git(repo_root, "worktree", "add", "-b", branch, str(path), base_commit)
        return RunWorkspace(
            path / subdir, branch, base_branch, base_commit, "worktree", dirty, base_label, notice
        )

    async def repo_root(self, path: Path) -> Path:
        return Path((await git(path, "rev-parse", "--show-toplevel")).stdout.strip())

    async def create_child(
        self, run_worktree: Path, run_branch: str, run_id: str, key: str
    ) -> tuple[Path, str]:
        repo_root = await self.repo_root(run_worktree)
        subdir = run_worktree.resolve().relative_to(repo_root.resolve())
        branch = f"{run_branch}--{branch_slug(key)}"
        path = self.root / f"{run_id}__{branch_slug(key)}"
        head = await self.head(repo_root)
        if path.exists():
            await self.remove(path, branch)
        existing = await git(
            repo_root, "rev-parse", "--verify", f"refs/heads/{branch}", check=False
        )
        if existing.code == 0:
            await git(repo_root, "branch", "-D", branch, check=False)
        await git(repo_root, "worktree", "add", "-b", branch, str(path), head)
        return path / subdir, branch

    async def commit_all(self, path: Path, message: str) -> str | None:
        root = await self.repo_root(path)
        # Stage everything the agent changed, except interpreter/test/editor caches
        # that repositories without a .gitignore would otherwise pick up.
        await git(root, "add", "-A", "--", ".", *(f":(exclude,glob){p}" for p in COMMIT_EXCLUDES))
        staged = await git(root, "diff", "--cached", "--name-only")
        if not staged.stdout.strip():
            return None
        await git(root, "commit", "--no-verify", "-q", "-m", message)
        return await self.head(root)

    async def merge_into(
        self, target: Path, branch: str, message: str
    ) -> tuple[bool, list[str], str | None]:
        """Merge `branch` into the worktree at `target`. Returns (ok, conflicts, merge_commit)."""
        root = await self.repo_root(target)
        result = await git(
            root, "merge", "--no-ff", "--no-edit", "-m", message, branch, check=False
        )
        if result.code == 0:
            return True, [], await self.head(root)
        conflicts = (
            await git(root, "diff", "--name-only", "--diff-filter=U", check=False)
        ).stdout.split()
        await git(root, "merge", "--abort", check=False)
        return False, conflicts, None

    async def diff(self, path: Path, base_commit: str) -> dict[str, object]:
        root = await self.repo_root(path)
        stat = await git(root, "diff", "--numstat", base_commit, check=False)
        names = await git(root, "diff", "--name-status", base_commit, check=False)
        patch = await git(root, "diff", "--no-color", "--patch", base_commit, check=False)
        log_out = await git(
            root, "log", "--format=%H%x1f%s%x1f%an%x1f%aI", f"{base_commit}..HEAD", check=False
        )
        files: dict[str, dict[str, Any]] = {}
        for line in names.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                files[parts[-1]] = {
                    "path": parts[-1],
                    "status": parts[0][0],
                    "additions": 0,
                    "deletions": 0,
                }
        for line in stat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] in files:
                files[parts[2]]["additions"] = int(parts[0]) if parts[0].isdigit() else 0
                files[parts[2]]["deletions"] = int(parts[1]) if parts[1].isdigit() else 0
        text = patch.stdout
        truncated = len(text) > MAX_DIFF_BYTES
        commits = []
        for line in log_out.stdout.splitlines():
            sha, subject, author, date = (line.split("\x1f") + ["", "", "", ""])[:4]
            commits.append({"sha": sha, "subject": subject, "author": author, "date": date})
        return {
            "files": list(files.values()),
            "patch": text[:MAX_DIFF_BYTES],
            "truncated": truncated,
            "commits": commits,
            "additions": sum(int(f["additions"]) for f in files.values()),
            "deletions": sum(int(f["deletions"]) for f in files.values()),
        }

    async def remove(self, path: Path, branch: str | None = None) -> None:
        try:
            root = await self.repo_root(path)
            common = Path((await git(root, "rev-parse", "--git-common-dir")).stdout.strip())
            main_repo = (
                (root / common).resolve().parent if not common.is_absolute() else common.parent
            )
        except (GitError, OSError):
            main_repo = None
        if main_repo is not None:
            await git(main_repo, "worktree", "remove", "--force", str(path), check=False)
            if branch:
                await git(main_repo, "branch", "-D", branch, check=False)
            await git(main_repo, "worktree", "prune", check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    async def integrate(
        self,
        workspace: Path,
        branch: str,
        base_branch: str | None,
        base_commit: str | None,
        message: str,
    ) -> str:
        """Merge an agent branch into the user's checkout.

        Only merges onto the branch the run started from, with a clean tree, when
        the run's starting commit is already on that branch (so a run based on a
        pull-request head never drags the whole PR in) and the agent branch has
        commits of its own.
        """
        root = await self.repo_root(workspace)
        current = await self.current_branch(root)
        if not base_branch:
            raise GitError(
                "This run did not start from a named branch (detached HEAD). "
                f"Merge the agent branch manually with `git merge {branch}`."
            )
        if current != base_branch:
            raise GitError(
                f"Your checkout is on '{current or 'a detached HEAD'}', but this run was based on "
                f"'{base_branch}'. Switch branches first or merge the agent branch manually."
            )
        if await self.is_dirty(root):
            raise GitError(
                "Your checkout has uncommitted changes. Commit or stash them before integrating."
            )
        if base_commit:
            on_branch = await git(
                root, "merge-base", "--is-ancestor", base_commit, "HEAD", check=False
            )
            if on_branch.code != 0:
                raise GitError(
                    f"This run started from {base_commit[:8]}, which is not on '{base_branch}' "
                    "(for example a pull-request head). Merging would bring those commits in too; "
                    f"review and merge `{branch}` manually instead."
                )
        ahead = await git(root, "rev-list", "--count", f"HEAD..{branch}", check=False)
        if ahead.code != 0 or ahead.stdout.strip() in ("", "0"):
            raise GitError("The agent branch has no commits that are not already on your branch.")
        ok, conflicts, commit = await self.merge_into(root, branch, message)
        if not ok:
            raise GitError(
                "Merge conflicts in "
                + ", ".join(conflicts[:10])
                + ". The merge was aborted; resolve manually with "
                f"`git merge {branch}`."
            )
        return commit or ""
