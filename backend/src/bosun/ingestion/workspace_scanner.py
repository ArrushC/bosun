"""Discover actionable work in local source trees.

Produces `DiscoveredItem`s for:
* annotation comments (TODO / FIXME / HACK / ... — configurable) in source code,
* unchecked Markdown checkboxes (`- [ ] ...`),
* entries in dedicated TODO files (TODO.md, todo.txt, TASKS.md, ...),
* task annotations written in documentation prose (`TODO: ...`, `> **FIXME** ...`).

Each item has a stable `external_id` derived from its path, tag, normalised
text and occurrence index, so re-scanning after unrelated edits (which shift
line numbers) updates the existing record instead of creating a duplicate.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from bosun.domain.enums import SourceKind
from bosun.ingestion.file_walker import WalkOptions, WalkStats, iter_workspace_files

CONTEXT_RADIUS = 3
MAX_CONTINUATION_LINES = 4
MAX_TITLE = 140

LINE_COMMENT_MARKERS: dict[str, tuple[str, ...]] = {}
for _exts, _markers in [
    (
        "c h cc cpp cxx hpp hh hxx ipp inl cu go js jsx ts tsx mjs cjs mts cts java kt kts scala "
        "swift rs cs dart php groovy gradle proto zig m mm scss less jsonc sol v vue svelte",
        ("//", "/*", "*"),
    ),
    ("css", ("/*", "*")),
    (
        "py pyi pyx rb sh bash zsh fish pl pm r yaml yml toml cmake mk dockerfile ps1 conf cfg "
        "nim ex exs jl tf hcl nix bzl bazel star gn gni",
        ("#",),
    ),
    ("sql lua hs elm ada", ("--",)),
    ("lisp clj cljs el scm asm s ini", (";",)),
    ("tex erl", ("%",)),
    ("html htm xml xhtml", ("<!--",)),
]:
    for _ext in _exts.split():
        LINE_COMMENT_MARKERS[_ext] = _markers

SPECIAL_FILENAMES: dict[str, tuple[str, ...]] = {
    "makefile": ("#",),
    "dockerfile": ("#",),
    "cmakelists.txt": ("#",),
    "justfile": ("#",),
    "build": ("#",),
    "workspace": ("#",),
    "gemfile": ("#",),
    "rakefile": ("#",),
}

MARKDOWN_EXTS = {"md", "markdown", "mdx", "rst"}
TODO_FILE_NAMES = {
    "todo",
    "todo.md",
    "todo.txt",
    "todos.md",
    "todos.txt",
    "tasks.md",
    "tasks.txt",
    "backlog.md",
}

LANGUAGE_NAMES = {
    "c": "C", "h": "C", "cc": "C++", "cpp": "C++", "cxx": "C++", "hpp": "C++", "hh": "C++",
    "py": "Python", "pyi": "Python", "go": "Go", "js": "JavaScript", "jsx": "JavaScript",
    "mjs": "JavaScript", "cjs": "JavaScript", "ts": "TypeScript", "tsx": "TypeScript",
    "java": "Java", "kt": "Kotlin", "rs": "Rust", "rb": "Ruby", "cs": "C#", "swift": "Swift",
    "sh": "Shell", "bash": "Shell", "md": "Markdown", "sql": "SQL", "lua": "Lua",
}  # fmt: skip


@dataclass
class DiscoveredItem:
    kind: SourceKind
    external_id: str
    title: str
    path: str
    line: int
    context: str
    tag: str | None = None
    body: str | None = None
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha1(f"{self.title}\0{self.body or ''}".encode()).hexdigest()[:16]


@dataclass
class ScanOptions:
    tags: list[str]
    exclude_globs: list[str]
    max_file_kb: int = 512
    max_files: int = 50_000
    include_markdown_tasks: bool = True
    include_todo_files: bool = True
    excluded_paths: list[Path] = field(default_factory=list)


@dataclass
class ScanResult:
    items: list[DiscoveredItem]
    files_scanned: int
    duration_ms: int
    stats: WalkStats
    git_branch: str | None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        by_kind: dict[str, int] = {}
        for item in self.items:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        return {
            "items": len(self.items),
            "by_kind": by_kind,
            "files_scanned": self.files_scanned,
            "files_considered": self.stats.files_considered,
            "skipped_excluded": self.stats.skipped_excluded,
            "skipped_large": self.stats.skipped_large,
            "skipped_binary": self.stats.skipped_binary,
            "skipped_symlink": self.stats.skipped_symlink,
            "truncated": self.stats.truncated,
            "used_git": self.stats.used_git,
            "duration_ms": self.duration_ms,
            "git_branch": self.git_branch,
            "errors": self.errors[:20],
        }


def _stable_id(*parts: object) -> str:
    raw = "\0".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:20]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _title(text: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    if len(text) <= MAX_TITLE:
        return text
    cut = text[:MAX_TITLE].rsplit(" ", 1)[0]
    return f"{cut}…"


def _context(lines: list[str], index: int) -> str:
    start = max(0, index - CONTEXT_RADIUS)
    end = min(len(lines), index + CONTEXT_RADIUS + 1)
    width = len(str(end))
    return "\n".join(
        f"{n + 1:>{width}}{'>' if n == index else ' '} {lines[n].rstrip()}"
        for n in range(start, end)
    )


def _comment_regex(markers: tuple[str, ...], tags: list[str]) -> re.Pattern[str]:
    marker_alt = "|".join(
        re.escape(m) + ("+" if m in ("//", "#", ";", "*") else "") for m in markers
    )
    tag_alt = "|".join(re.escape(t) for t in tags)
    # `*` continuation markers only count at the start of a line.
    return re.compile(
        rf"(?:(?:^|\s)(?P<marker>{marker_alt}))\s*@?(?P<tag>{tag_alt})\b"
        r"(?:\((?P<owner>[^)]{0,60})\))?:?\s*[-–:]?\s*(?P<text>.*)$"
    )


_TRAILERS = re.compile(r"\s*(?:\*/|-->|\*+)\s*$")


def _comment_body(line: str, markers: tuple[str, ...]) -> str | None:
    """If `line` is a comment-only line, return its text (for continuations)."""
    stripped = line.strip()
    for marker in markers:
        if marker == "*" and stripped.startswith("*/"):
            return None
        if stripped.startswith(marker):
            body = stripped.lstrip(marker[0]).lstrip(marker)
            return _TRAILERS.sub("", body).strip()
    return None


def _scan_source(
    rel: str, lines: list[str], markers: tuple[str, ...], tags: list[str], ext: str
) -> list[DiscoveredItem]:
    pattern = _comment_regex(markers, tags)
    tag_anywhere = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in tags) + r")\b")
    items: list[DiscoveredItem] = []
    seen: dict[tuple[str, str], int] = {}
    for idx, line in enumerate(lines):
        if not any(t in line for t in tags):
            continue
        match = pattern.search(line)
        if not match:
            continue
        marker = match.group("marker")
        if marker.startswith("*") and line[: match.start("marker")].strip():
            continue  # `a * TODO` is multiplication, not a comment
        tag = match.group("tag")
        text = _TRAILERS.sub("", match.group("text") or "").strip()
        # Continuation lines: subsequent comment-only lines without their own tag.
        extra: list[str] = []
        for follow in lines[idx + 1 : idx + 1 + MAX_CONTINUATION_LINES]:
            body = _comment_body(follow, markers)
            if not body or tag_anywhere.search(body):
                break
            extra.append(body)
        full_text = " ".join([text, *extra]).strip()
        key = (tag, _normalise(full_text))
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        meta: dict[str, object] = {
            "marker": marker,
            "language": LANGUAGE_NAMES.get(ext, ext or "text"),
        }
        if match.group("owner"):
            meta["owner"] = match.group("owner").strip()
        items.append(
            DiscoveredItem(
                kind=SourceKind.CODE_TODO,
                external_id=_stable_id("code", rel, tag, key[1], occurrence),
                title=_title(full_text, f"{tag} in {rel}:{idx + 1}"),
                body=full_text or None,
                path=rel,
                line=idx + 1,
                context=_context(lines, idx),
                tag=tag,
                meta=meta,
            )
        )
    return items


_CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[(?P<state>[ xX~-])\]\s+(?P<text>.+?)\s*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _doc_annotation_regex(tags: list[str]) -> re.Pattern[str]:
    tag_alt = "|".join(re.escape(t) for t in tags)
    return re.compile(
        rf"^\s*(?:>\s*)?(?:[-*+]\s+)?(?:\*\*|__)?(?P<tag>{tag_alt})(?:\*\*|__)?\s*(?:\((?P<owner>[^)]{{0,60}})\))?\s*[:\-–]\s*(?P<text>.+?)\s*(?:-->)?$"
    )


def _scan_markdown(
    rel: str, lines: list[str], tags: list[str], include_checkboxes: bool
) -> list[DiscoveredItem]:
    items: list[DiscoveredItem] = []
    annotation = _doc_annotation_regex(tags)
    html_comment = _comment_regex(("<!--",), tags)
    section: str | None = None
    in_fence = False
    seen: dict[tuple[str, str, str], int] = {}
    for idx, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING.match(line)
        if heading:
            section = heading.group("text").strip()
            continue
        text: str | None = None
        tag: str | None = None
        owner: str | None = None
        if include_checkboxes and (box := _CHECKBOX.match(line)):
            if box.group("state") != " ":
                continue
            text = box.group("text")
        elif (m := annotation.match(line)) or (m := html_comment.search(line)):
            tag = m.group("tag")
            text = _TRAILERS.sub("", m.group("text") or "").strip()
            owner = m.group("owner")
        if not text:
            continue
        key = (tag or "checkbox", section or "", _normalise(text))
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        meta: dict[str, object] = {"section": section} if section else {}
        if owner:
            meta["owner"] = owner.strip()
        items.append(
            DiscoveredItem(
                kind=SourceKind.MARKDOWN_TASK,
                external_id=_stable_id("md", rel, *key, occurrence),
                title=_title(text, f"Task in {rel}:{idx + 1}"),
                body=text,
                path=rel,
                line=idx + 1,
                context=_context(lines, idx),
                tag=tag,
                meta=meta,
            )
        )
    return items


_TODO_LINE = re.compile(
    r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?(?:\[(?P<state>[ xX~-])\]\s+)?(?P<text>.*\S)\s*$"
)


def _scan_todo_file(rel: str, lines: list[str]) -> list[DiscoveredItem]:
    items: list[DiscoveredItem] = []
    section: str | None = None
    seen: dict[str, int] = {}
    for idx, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading:
            section = heading.group("text").strip()
            continue
        if not line.strip() or line.strip().startswith(("<!--", "---", "```")):
            continue
        m = _TODO_LINE.match(line)
        if not m:
            continue
        text = m.group("text").strip()
        if m.group("state") and m.group("state") != " ":
            continue
        if re.match(r"^x\s", text):  # todo.txt completed entries
            continue
        if line[: len(line) - len(line.lstrip())] and not m.group(0).lstrip().startswith(
            ("-", "*", "+")
        ):
            continue  # indented prose continuation
        norm = _normalise(text)
        occurrence = seen.get(norm, 0)
        seen[norm] = occurrence + 1
        items.append(
            DiscoveredItem(
                kind=SourceKind.TODO_FILE,
                external_id=_stable_id("todo-file", rel, norm, occurrence),
                title=_title(text, f"Entry in {rel}:{idx + 1}"),
                body=text,
                path=rel,
                line=idx + 1,
                context=_context(lines, idx),
                meta={"section": section} if section else {},
            )
        )
    return items


def git_branch(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def scan_file(root: Path, path: Path, options: ScanOptions) -> list[DiscoveredItem]:
    rel = path.relative_to(root).as_posix()
    name = path.name.lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    if name in TODO_FILE_NAMES:
        return _scan_todo_file(rel, lines) if options.include_todo_files else []
    if ext in MARKDOWN_EXTS:
        return _scan_markdown(rel, lines, options.tags, options.include_markdown_tasks)
    markers = SPECIAL_FILENAMES.get(name) or LINE_COMMENT_MARKERS.get(ext)
    if not markers:
        return []
    return _scan_source(rel, lines, markers, options.tags, ext)


def scan_workspace(root: Path, options: ScanOptions) -> ScanResult:
    started = time.monotonic()
    root = root.resolve()
    stats = WalkStats()
    walk = WalkOptions(
        exclude_globs=options.exclude_globs,
        max_file_bytes=options.max_file_kb * 1024,
        max_files=options.max_files,
        excluded_paths=options.excluded_paths,
    )
    items: list[DiscoveredItem] = []
    errors: list[str] = []
    scanned = 0
    for path in iter_workspace_files(root, walk, stats):
        scanned += 1
        try:
            items.extend(scan_file(root, path, options))
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the scan
            errors.append(f"{path}: {exc}")
    return ScanResult(
        items=items,
        files_scanned=scanned,
        duration_ms=int((time.monotonic() - started) * 1000),
        stats=stats,
        git_branch=git_branch(root) if stats.used_git else None,
        errors=errors,
    )
