from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bosun.domain.enums import SourceKind
from bosun.ingestion.workspace_scanner import (
    DiscoveredItem,
    ScanOptions,
    ScanResult,
    scan_workspace,
)
from bosun.settings_store import DEFAULT_EXCLUDES

TAGS = ["TODO", "FIXME", "HACK", "XXX", "BUG"]


def opts(**kw) -> ScanOptions:
    return ScanOptions(tags=TAGS, exclude_globs=list(DEFAULT_EXCLUDES), **kw)


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def by_title(result: ScanResult) -> dict[str, DiscoveredItem]:
    return {item.title: item for item in result.items}


def test_extracts_comment_annotations_across_languages(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", "def f():\n    # TODO: handle empty input\n    return 1\n")
    write(tmp_path, "src/lib.c", "int x; /* FIXME(alice): overflow on 32-bit */\n")
    write(tmp_path, "src/main.go", "package main\n// HACK - temporary workaround\nfunc main() {}\n")
    write(tmp_path, "src/index.ts", "const a = 1; // TODO wire up the API client\n")
    write(tmp_path, "src/Main.java", "/**\n * TODO: document this class\n */\nclass Main {}\n")
    write(tmp_path, "src/util.cpp", "int y = a * TODO_COUNT; // nothing here\n")

    result = scan_workspace(tmp_path, opts())
    titles = by_title(result)

    assert set(titles) == {
        "handle empty input",
        "overflow on 32-bit",
        "temporary workaround",
        "wire up the API client",
        "document this class",
    }
    fixme = titles["overflow on 32-bit"]
    assert fixme.tag == "FIXME"
    assert fixme.meta["owner"] == "alice"
    assert fixme.path == "src/lib.c" and fixme.line == 1
    py = titles["handle empty input"]
    assert py.line == 2
    assert "2>" in py.context  # context marks the annotated line
    assert all(i.kind == SourceKind.CODE_TODO for i in result.items)


def test_multiline_comment_continuation(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "# TODO: refactor this to avoid\n#   the double allocation\n# FIXME: separate item\nx = 1\n",
    )
    titles = by_title(scan_workspace(tmp_path, opts()))
    assert "refactor this to avoid the double allocation" in titles
    assert "separate item" in titles


def test_markdown_checkboxes_and_doc_annotations(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/plan.md",
        "# Plan\n\n## Release\n- [ ] Write changelog\n- [x] Tag release\n"
        "* [ ] Announce\n\nTODO: document the config file\n\n```\n- [ ] inside code fence\n```\n"
        "<!-- FIXME: broken link below -->\n",
    )
    result = scan_workspace(tmp_path, opts())
    titles = by_title(result)
    assert set(titles) == {
        "Write changelog",
        "Announce",
        "document the config file",
        "broken link below",
    }
    assert titles["Write changelog"].meta["section"] == "Release"
    assert titles["document the config file"].tag == "TODO"
    assert all(i.kind == SourceKind.MARKDOWN_TASK for i in result.items)


def test_dedicated_todo_files(tmp_path: Path) -> None:
    write(tmp_path, "TODO.md", "# Next\n- Add retries\n- [ ] Cache tokens\n- [x] Done thing\n")
    write(tmp_path, "todo.txt", "(A) Call the bank +finance\nx 2024-01-01 finished\n")
    result = scan_workspace(tmp_path, opts())
    titles = by_title(result)
    assert set(titles) == {"Add retries", "Cache tokens", "(A) Call the bank +finance"}
    assert all(i.kind == SourceKind.TODO_FILE for i in result.items)


def test_stable_identity_survives_line_shifts(tmp_path: Path) -> None:
    path = write(tmp_path, "a.py", "# TODO: one\nx = 1\n# TODO: two\n")
    first = {i.title: i.external_id for i in scan_workspace(tmp_path, opts()).items}
    path.write_text("import os\n\n\n# TODO: one\nx = 1\n# TODO: two\n")
    second = {i.title: i.external_id for i in scan_workspace(tmp_path, opts()).items}
    assert first == second


def test_duplicate_annotations_get_distinct_ids(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "# TODO: same\n# TODO: same\n")
    items = scan_workspace(tmp_path, opts()).items
    assert len(items) == 2
    assert items[0].external_id != items[1].external_id


def test_exclusions_size_limit_and_binary(tmp_path: Path) -> None:
    write(tmp_path, "node_modules/pkg/index.js", "// TODO: vendored\n")
    write(tmp_path, "build/out.py", "# TODO: generated\n")
    write(tmp_path, "big.py", "# TODO: big\n" + "x = 1\n" * 300_000)
    (tmp_path / "bin.c").write_bytes(b"// TODO: binary\0\0\0")
    write(tmp_path, "ok.py", "# TODO: keep me\n")
    result = scan_workspace(tmp_path, opts(max_file_kb=64))
    assert [i.title for i in result.items] == ["keep me"]
    assert result.stats.skipped_large == 1
    assert result.stats.skipped_binary == 1


def test_excluded_paths_skip_bosun_itself(tmp_path: Path) -> None:
    write(tmp_path, "bosun/backend/app.py", "# TODO: internal\n")
    write(tmp_path, "project/app.py", "# TODO: external\n")
    result = scan_workspace(tmp_path, opts(excluded_paths=[tmp_path / "bosun"]))
    assert [i.title for i in result.items] == ["external"]


def test_respects_gitignore_without_git(tmp_path: Path) -> None:
    write(tmp_path, ".gitignore", "secret/\n*.gen.py\n")
    write(tmp_path, "secret/a.py", "# TODO: hidden\n")
    write(tmp_path, "x.gen.py", "# TODO: generated\n")
    write(tmp_path, "sub/.gitignore", "local.py\n")
    write(tmp_path, "sub/local.py", "# TODO: nested ignore\n")
    write(tmp_path, "sub/kept.py", "# TODO: visible\n")
    result = scan_workspace(tmp_path, opts())
    assert [i.title for i in result.items] == ["visible"]
    assert not result.stats.used_git


def test_git_repo_uses_ls_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path, ".gitignore", "ignored.py\n")
    write(tmp_path, "ignored.py", "# TODO: ignored\n")
    write(tmp_path, "tracked.py", "# TODO: tracked\n")
    result = scan_workspace(tmp_path, opts())
    assert result.stats.used_git
    assert [i.title for i in result.items] == ["tracked"]


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_symlinks_outside_root_are_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write(outside, "secret.py", "# TODO: outside root\n")
    root = tmp_path / "root"
    write(root, "inside.py", "# TODO: inside\n")
    (root / "link.py").symlink_to(outside / "secret.py")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    result = scan_workspace(root, opts())
    assert [i.title for i in result.items] == ["inside"]
