from __future__ import annotations

"""Check 项目大脑 first-level index sizes."""

import argparse
import datetime as dt
import json
import posixpath
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


INFO_DIR = "项目大脑"
USER_DIR = "用户"
DEFAULT_MAX_ENTRIES = 50
SECTION_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$")
CODE_SPAN_PATTERN = re.compile(r"`([^`]+)`")


@dataclass
class IndexSize:
    path: str
    entries: int
    max_entries: int
    oversized: bool
    auto_split_applied: bool = False


def split_markdown_row(line: str) -> list[str]:
    content = line.strip()[1:-1]
    cells: list[str] = []
    buffer: list[str] = []
    escaped = False
    for char in content:
        if escaped:
            buffer.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
    if escaped:
        buffer.append("\\")
    cells.append("".join(buffer).strip())
    return cells


def is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(cell.replace(":", "").strip()) <= {"-"} for cell in cells)


def count_index_entries(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = split_markdown_row(stripped)
        if is_separator(cells):
            continue
        if not cells or cells[0] in {"文档", "Skill", "目录"}:
            continue
        if cells[0].startswith("`"):
            count += 1
    return count


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(f"Cannot find repo root from {start}")


def find_first_level_indexes(repo_root: Path) -> list[Path]:
    info_root = repo_root / INFO_DIR
    indexes = [info_root / "index.md"]
    users_root = info_root / USER_DIR
    if users_root.exists():
        indexes.extend(sorted(path / "index.md" for path in users_root.iterdir() if path.is_dir()))
    return [path for path in indexes if path.exists()]


def collect_sizes(repo_root: Path, max_entries: int) -> list[IndexSize]:
    result: list[IndexSize] = []
    for path in find_first_level_indexes(repo_root):
        entries = count_index_entries(path)
        relative = path.relative_to(repo_root).as_posix()
        result.append(
            IndexSize(
                path=relative,
                entries=entries,
                max_entries=max_entries,
                oversized=entries > max_entries,
            )
        )
    return result


def should_rewrite_code_path(value: str) -> bool:
    if not value or value.startswith(("http://", "https://", "#")):
        return False
    if " " in value or "\t" in value:
        return False
    return "/" in value or "." in value or value.endswith("/")


def rewrite_code_paths_for_secondary(row: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(1)
        if not should_rewrite_code_path(value):
            return match.group(0)
        has_trailing_slash = value.endswith("/")
        rewritten = posixpath.normpath(posixpath.join("..", value))
        if has_trailing_slash and not rewritten.endswith("/"):
            rewritten += "/"
        return f"`{rewritten}`"

    return CODE_SPAN_PATTERN.sub(replace, row)


def slugify_heading(heading: str) -> str:
    slug = re.sub(r"[`*_{}\[\]()<>:：/\\|?？!！,，.。;；\"']", "", heading).strip()
    slug = re.sub(r"\s+", "-", slug)
    return slug or "未分类"


def secondary_root_for_index(repo_root: Path, index_path: Path) -> Path:
    info_root = repo_root / INFO_DIR
    public_index = info_root / "index.md"
    if index_path.resolve() == public_index.resolve():
        return info_root / "索引"
    return index_path.parent / "索引"


def extract_section_entries(lines: list[str]) -> list[str]:
    entries: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = split_markdown_row(stripped)
        if is_separator(cells):
            continue
        if not cells or cells[0] in {"文档", "Skill", "目录"}:
            continue
        if cells[0].startswith("`"):
            entries.append(line)
    return entries


def extract_non_entry_lines(lines: list[str]) -> list[str]:
    kept: list[str] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            in_table = True
            continue
        if in_table and not stripped:
            in_table = False
            continue
        if not in_table:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return kept


def split_index(repo_root: Path, index_path: Path, max_entries: int) -> bool:
    if count_index_entries(index_path) <= max_entries:
        return False

    original_lines = index_path.read_text(encoding="utf-8-sig").splitlines()
    sections: list[tuple[str | None, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    for line in original_lines:
        match = SECTION_HEADING_PATTERN.match(line)
        if match:
            sections.append((current_heading, current_lines))
            current_heading = match.group(1)
            current_lines = [line]
            continue
        current_lines.append(line)
    sections.append((current_heading, current_lines))

    secondary_root = secondary_root_for_index(repo_root, index_path)
    secondary_root.mkdir(parents=True, exist_ok=True)
    updated_lines: list[str] = []
    split_any = False
    today = dt.date.today().isoformat()
    relative_index = index_path.relative_to(repo_root).as_posix()

    for heading, lines in sections:
        if heading is None:
            updated_lines.extend(lines)
            continue
        entries = extract_section_entries(lines)
        if not entries:
            updated_lines.extend(lines)
            continue

        slug = slugify_heading(heading)
        secondary_path = secondary_root / f"{slug}.md"
        rewritten_entries = [rewrite_code_paths_for_secondary(row) for row in entries]
        secondary_path.write_text(
            f"# {heading}索引\n\n"
            f"> 自动分级来源: `{relative_index}`\n"
            f"> 更新时间: {today}\n\n"
            "| 文档 | 用途 |\n"
            "| --- | --- |\n"
            + "\n".join(rewritten_entries)
            + "\n",
            encoding="utf-8",
        )

        replacement_path = secondary_path.relative_to(index_path.parent).as_posix()
        non_entry_lines = extract_non_entry_lines(lines)
        updated_lines.extend(non_entry_lines)
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.extend(
            [
                "| 文档 | 用途 |",
                "| --- | --- |",
                f"| `{replacement_path}` | {heading}二级索引；自动承载本节 {len(entries)} 条入口。 |",
            ]
        )
        split_any = True

    if split_any:
        index_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")
    return split_any


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check first-level project-info index sizes.")
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    parser.add_argument("--auto-split", action="store_true", help="Automatically split oversized first-level indexes.")
    parser.add_argument("--fail-on-oversized", action="store_true", help="Return non-zero when oversized indexes remain.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    sizes = collect_sizes(repo_root, args.max_entries)
    applied: list[str] = []
    if args.auto_split:
        split_paths = {item.path for item in sizes if item.oversized}
        for path in find_first_level_indexes(repo_root):
            relative = path.relative_to(repo_root).as_posix()
            if relative in split_paths and split_index(repo_root, path, args.max_entries):
                applied.append(relative)
        sizes = collect_sizes(repo_root, args.max_entries)
        for item in sizes:
            item.auto_split_applied = item.path in split_paths and not item.oversized
    oversized = [item for item in sizes if item.oversized]

    if args.json:
        print(json.dumps([asdict(item) for item in sizes], ensure_ascii=False, indent=2))
    else:
        for relative in applied:
            print(f"AUTO_SPLIT {relative}")
        for item in sizes:
            status = "SPLIT_NEEDED" if item.oversized else "OK"
            if item.auto_split_applied:
                status = "AUTO_SPLIT_OK"
            print(f"{status} {item.path}: entries={item.entries} max={item.max_entries}")
    return 1 if oversized and args.fail_on_oversized else 0


if __name__ == "__main__":
    raise SystemExit(main())
