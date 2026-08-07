from __future__ import annotations

"""Generate root workstate.json for 项目大脑.

Usage:
    python 项目大脑/skills/generate-workstate/scripts/generate_workstate.py --author {author}
    python 项目大脑/skills/generate-workstate/scripts/generate_workstate.py --author {author} --task-folder 项目大脑/用户/{author}/tasks/YYYY-MM-DD-example/
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
        raise SystemExit(f"Invalid workstate.json: expected object")
    author = data.get("author")
    if author is not None and not isinstance(author, str):
        raise SystemExit(f"Invalid author in {path}: expected string")
    return author


def normalize_relative_path(repo_root: Path, path_text: str) -> str:
    raw_path = Path(path_text)
    path = raw_path if raw_path.is_absolute() else repo_root / raw_path
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path is outside repo: {path}") from exc
    return relative.as_posix().rstrip("/") + "/"


def validate_task_folder(repo_root: Path, author: str, task_folder: str) -> str:
    relative = normalize_relative_path(repo_root, task_folder)
    folder = repo_root / relative
    if not folder.exists() or not folder.is_dir():
        raise SystemExit(f"Task folder does not exist: {relative}")

    expected_prefix = f"项目大脑/用户/{author}/"
    if not relative.startswith(expected_prefix):
        raise SystemExit(
            "Task folder must be under "
            f"{expected_prefix}; got {relative}"
        )

    return relative


def build_workstate(
    author: str,
    task_folder: str,
    source: str,
    git_user_name: str,
    git_user_email: str,
    remote: str,
) -> dict[str, Any]:
    return {
        "author": author,
        "task_folder": task_folder,
        "git_user_name": git_user_name,
        "git_user_email": git_user_email,
        "remote": remote,
        "source": source,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "Local cold-start state. Do not commit.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate root workstate.json for project-info cold startup."
    )
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument("--author", help="Current remote account / author folder name.")
    parser.add_argument(
        "--task-folder",
        default="",
        help=(
            "Current concrete task folder path. Leave empty during cold start before "
            "a task is selected."
        ),
    )
    parser.add_argument(
        "--source",
        default="manual-confirmed",
        help="State source label written into workstate.json.",
    )
    parser.add_argument(
        "--git-user-name",
        help="Git user.name to record in workstate.json. Defaults to author.",
    )
    parser.add_argument(
        "--git-user-email",
        default="",
        help="Git user.email to record in workstate.json when known.",
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="Git remote name associated with this workstate.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    author = args.author or load_workstate_author(repo_root)
    if not author:
        raise SystemExit("Author is required. Pass --author or create workstate.json.")

    task_folder = validate_task_folder(repo_root, author, args.task_folder) if args.task_folder else ""
    workstate = build_workstate(
        author=author,
        task_folder=task_folder,
        source=args.source,
        git_user_name=args.git_user_name or author,
        git_user_email=args.git_user_email,
        remote=args.remote,
    )
    output = repo_root / "workstate.json"
    output.write_text(
        json.dumps(workstate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
