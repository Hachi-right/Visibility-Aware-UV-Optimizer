from __future__ import annotations

"""Ensure project-info extension directories exist.

Default behavior only creates current-author private extension directories.
Public directories are reserved for the leader publicization flow.
"""

import argparse
import json
import re
from pathlib import Path


AUTHOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EXTENSION_DIRS = ("skills", "mcps", "hooks")
PUBLIC_EXTENSION_AUTHOR = "tech-lead"


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(f"Cannot find repo root from {start}")


def load_workstate_author(repo_root: Path) -> str | None:
    path = repo_root / "workstate.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Invalid workstate.json: expected object")
    author = data.get("author")
    if author is not None and not isinstance(author, str):
        raise SystemExit(f"Invalid author in {path}: expected string")
    return author


def validate_author(author: str) -> str:
    if not AUTHOR_PATTERN.fullmatch(author):
        raise SystemExit(
            "Invalid author. Use 1-64 characters: letters, digits, dot, underscore, hyphen; "
            "the first character must be a letter or digit."
        )
    return author


def normalize_relative_path(repo_root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path is outside repo: {path}") from exc
    return relative.as_posix().rstrip("/") + "/"


def write_gitkeep(path: Path) -> None:
    marker = path / ".gitkeep"
    if not marker.exists():
        marker.write_text("keep directory\n", encoding="utf-8")


def extension_paths(repo_root: Path, author: str | None, scope: str) -> list[Path]:
    paths: list[Path] = []
    if scope in {"all", "public"}:
        paths.extend(repo_root / "项目大脑" / name for name in EXTENSION_DIRS)
    if scope in {"all", "user"}:
        if not author:
            raise SystemExit("Author is required for user extension directories.")
        paths.extend(repo_root / "项目大脑" / "用户" / author / name for name in EXTENSION_DIRS)
    return paths


def ensure_dirs(repo_root: Path, author: str | None, scope: str, dry_run: bool) -> None:
    for path in extension_paths(repo_root, author, scope):
        relative = normalize_relative_path(repo_root, path)
        if dry_run:
            print(f"Would ensure {relative}")
            continue
        path.mkdir(parents=True, exist_ok=True)
        if path.name != "skills":
            write_gitkeep(path)
        print(f"Ensured {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure project-info extension directories.")
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument("--author", help="Current remote account / author folder name.")
    parser.add_argument(
        "--scope",
        choices=("all", "public", "user"),
        default="user",
        help="Which extension directory set to create.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print paths without writing files.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    author = args.author or load_workstate_author(repo_root)
    if args.scope in {"all", "user", "public"}:
        author = validate_author(author or "")
    if args.scope in {"all", "public"} and author != PUBLIC_EXTENSION_AUTHOR:
        raise SystemExit(
            "Public extension directories can only be created by the leader publicization flow "
            f"with --author {PUBLIC_EXTENSION_AUTHOR}."
        )
    ensure_dirs(repo_root, author, args.scope, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
