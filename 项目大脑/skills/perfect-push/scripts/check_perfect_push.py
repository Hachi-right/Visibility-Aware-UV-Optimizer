#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class StatusSummary:
    staged: int
    unstaged: int
    untracked: int
    conflicts: int
    preview: list[str]


@dataclass
class DiffMetrics:
    files: int
    insertions: int
    deletions: int
    binary_files: int


@dataclass
class CommitInfo:
    oid: str
    short: str
    subject: str


@dataclass
class NameStatusEntry:
    status: str
    path: str
    old_path: str | None = None


CONFLICT_STATUS_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
MAIN_BRANCHES = {"main", "master"}
DEFAULT_TARGET_LOOKBACK_COMMITS = 30
DEFAULT_REQUIRED_VALIDATION_CATEGORIES = ("static", "integration")
DEFAULT_ALLOWED_VALIDATION_PREFIXES = (
    "python -m py_compile ",
    "python 项目大脑/skills/perfect-push/scripts/test_check_perfect_push.py",
    "python \"项目大脑/skills/perfect-push/scripts/test_check_perfect_push.py\"",
    "git diff --check",
)


def run_git(
    repo: Path,
    args: list[str],
    check: bool = False,
    *,
    no_replace_objects: bool = False,
) -> GitResult:
    env = os.environ.copy()
    if no_replace_objects:
        env["GIT_NO_REPLACE_OBJECTS"] = "1"
    completed = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result = GitResult(completed.returncode, completed.stdout.strip(), completed.stderr.strip())
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr or result.stdout}")
    return result


def run_git_preserve_stdout(repo: Path, args: list[str], check: bool = False) -> GitResult:
    completed = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result = GitResult(completed.returncode, completed.stdout.rstrip("\n"), completed.stderr.strip())
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr or result.stdout}")
    return result


def find_repo_root(start: Path) -> Path:
    result = run_git(start, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise SystemExit(f"Cannot find git repository from {start}: {result.stderr}")
    return Path(result.stdout).resolve()


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", older, newer]).returncode == 0


def is_ancestor_no_replace(repo: Path, older: str, newer: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", older, newer], no_replace_objects=True).returncode == 0


def ref_exists(repo: Path, ref: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", ref]).returncode == 0


def object_type(repo: Path, ref: str) -> str | None:
    result = run_git(repo, ["cat-file", "-t", ref])
    return result.stdout if result.returncode == 0 and result.stdout else None


def matching_refs(repo: Path, ref: str) -> list[str]:
    candidates = [f"refs/heads/{ref}", f"refs/remotes/{ref}"]
    return [candidate for candidate in candidates if ref_exists(repo, candidate)]


def replace_refs(repo: Path) -> list[str]:
    result = run_git(repo, ["for-each-ref", "--format=%(refname)", "refs/replace"])
    return result.stdout.splitlines() if result.returncode == 0 and result.stdout else []


def short_ref(repo: Path, ref: str) -> str:
    result = run_git(repo, ["rev-parse", "--short", ref])
    return result.stdout if result.returncode == 0 else "<missing>"


def full_ref(repo: Path, ref: str) -> str:
    result = run_git(repo, ["rev-parse", ref])
    return result.stdout if result.returncode == 0 else "<missing>"


def list_commits(repo: Path, base: str, source: str, limit: int) -> list[str]:
    result = run_git(repo, ["log", "--oneline", "--decorate", f"--max-count={limit}", f"{base}..{source}"])
    return result.stdout.splitlines() if result.stdout else []


def list_commit_infos(repo: Path, base: str, source: str) -> list[CommitInfo]:
    result = run_git(repo, ["log", "--format=%H%x09%h%x09%s", f"{base}..{source}"])
    infos: list[CommitInfo] = []
    if result.returncode != 0 or not result.stdout:
        return infos
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        infos.append(
            CommitInfo(
                oid=parts[0],
                short=parts[1],
                subject=parts[2],
            )
        )
    return infos


def list_merge_commits(repo: Path, base: str, source: str) -> list[str]:
    result = run_git(repo, ["rev-list", "--merges", "--oneline", f"{base}..{source}"])
    return result.stdout.splitlines() if result.returncode == 0 and result.stdout else []


def diff_stat(repo: Path, base: str, source: str) -> str:
    result = run_git(repo, ["diff", "--stat", "--find-renames", f"{base}..{source}"])
    return result.stdout


def diff_metrics(repo: Path, base: str, source: str) -> DiffMetrics:
    result = run_git(repo, ["diff", "--numstat", "--find-renames", f"{base}..{source}"])
    files = 0
    insertions = 0
    deletions = 0
    binary_files = 0
    if result.returncode != 0 or not result.stdout:
        return DiffMetrics(0, 0, 0, 0)
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0] == "-" or parts[1] == "-":
            binary_files += 1
            continue
        insertions += int(parts[0])
        deletions += int(parts[1])
    return DiffMetrics(files, insertions, deletions, binary_files)


def changed_paths(repo: Path, base: str, source: str) -> list[str]:
    result = run_git(repo, ["diff", "--name-only", "--find-renames", f"{base}..{source}"])
    return result.stdout.splitlines() if result.returncode == 0 and result.stdout else []


def name_status_entries(repo: Path, base: str, source: str) -> list[NameStatusEntry]:
    result = run_git(repo, ["diff", "--name-status", "--find-renames", f"{base}..{source}"])
    entries: list[NameStatusEntry] = []
    if result.returncode != 0 or not result.stdout:
        return entries
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            entries.append(NameStatusEntry(status=status, old_path=parts[1], path=parts[2]))
        elif len(parts) >= 2:
            entries.append(NameStatusEntry(status=status, path=parts[1]))
    return entries


def recent_changed_paths(repo: Path, ref: str, max_commits: int) -> list[str]:
    if max_commits <= 0:
        return []
    result = run_git(repo, ["log", "--name-only", "--format=", f"--max-count={max_commits}", ref])
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(set(paths))


def case_collisions(paths: list[str]) -> list[list[str]]:
    grouped: dict[str, list[str]] = {}
    for path in paths:
        grouped.setdefault(path.lower(), []).append(path)
    return [sorted(set(values)) for values in grouped.values() if len(set(values)) > 1]


def git_dir(repo: Path) -> Path:
    result = run_git(repo, ["rev-parse", "--git-dir"])
    path_text = result.stdout if result.returncode == 0 else ".git"
    path = Path(path_text)
    return path if path.is_absolute() else repo / path


def git_operation_state(repo: Path) -> dict:
    dot_git = git_dir(repo)
    state = {
        "merge": (dot_git / "MERGE_HEAD").exists(),
        "rebase_apply": (dot_git / "rebase-apply").exists(),
        "rebase_merge": (dot_git / "rebase-merge").exists(),
        "cherry_pick": (dot_git / "CHERRY_PICK_HEAD").exists(),
        "revert": (dot_git / "REVERT_HEAD").exists(),
    }
    state["in_progress"] = any(state.values())
    return state


def hidden_index_flags(repo: Path) -> list[dict[str, str]]:
    result = run_git(repo, ["ls-files", "-v"])
    entries: list[dict[str, str]] = []
    if result.returncode != 0 or not result.stdout:
        return entries
    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        flag = line[0]
        if flag in {"h", "S"}:
            entries.append({"flag": flag, "path": line[2:]})
    return entries


def diff_check(repo: Path, base: str, source: str) -> GitResult:
    return run_git(repo, ["diff", "--check", f"{base}..{source}"])


def fetch_remote_tracking(repo: Path, remote: str, branch: str) -> GitResult:
    return run_git(repo, ["fetch", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"])


def remote_head_probe(repo: Path, remote: str, branch: str) -> GitResult:
    return run_git(repo, ["ls-remote", "--exit-code", "--heads", remote, branch])


def current_branch(repo: Path) -> str:
    result = run_git(repo, ["branch", "--show-current"])
    return result.stdout or "HEAD"


def upstream_ref(repo: Path) -> str | None:
    result = run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    return result.stdout if result.returncode == 0 and result.stdout else None


def porcelain_status(repo: Path) -> str:
    return run_git_preserve_stdout(repo, ["status", "--short"]).stdout


def summarize_status(status_text: str, max_preview: int = 12) -> StatusSummary:
    staged = 0
    unstaged = 0
    untracked = 0
    conflicts = 0
    lines = [line for line in status_text.splitlines() if line.strip()]
    for line in lines:
        code = line[:2]
        if code == "??":
            untracked += 1
            continue
        if code in CONFLICT_STATUS_CODES or "U" in code:
            conflicts += 1
        if len(code) >= 1 and code[0] not in {" ", "?"}:
            staged += 1
        if len(code) >= 2 and code[1] not in {" ", "?"}:
            unstaged += 1
    return StatusSummary(
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicts=conflicts,
        preview=lines[:max_preview],
    )


def rev_list_divergence(repo: Path, left: str, right: str) -> tuple[int, int] | None:
    result = run_git(repo, ["rev-list", "--left-right", "--count", f"{left}...{right}"])
    if result.returncode != 0 or not result.stdout:
        return None
    parts = result.stdout.split()
    if len(parts) != 2:
        return None
    return int(parts[0]), int(parts[1])


def print_section(title: str) -> None:
    print(f"\n## {title}")


def print_command_list(commands: list[str]) -> None:
    for command in commands:
        print(f"- {command}")


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_validation_manifest(
    path_text: str | None,
    repo: Path,
    head_sha: str,
    target_sha: str,
    required_categories: list[str],
    allowed_prefixes: list[str],
) -> dict:
    status = {
        "path": path_text,
        "valid": False,
        "errors": [],
        "trusted": False,
        "required_categories": required_categories,
        "matched_categories": [],
        "commands_count": 0,
    }
    if not path_text:
        return status

    path = Path(path_text).resolve()
    status["path"] = str(path)
    try:
        path.relative_to(repo)
        status["trusted"] = True
    except ValueError:
        status["errors"].append("validation_manifest_untrusted")
    git_path = git_dir(repo).resolve()
    try:
        path.relative_to(git_path)
        status["errors"].append("validation_manifest_in_git_dir")
        status["trusted"] = False
    except ValueError:
        pass
    if not path.exists():
        status["errors"].append("validation_manifest_missing")
        return status
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        status["errors"].append(f"validation_manifest_unreadable:{exc}")
        return status

    if manifest.get("head_sha") != head_sha:
        status["errors"].append("validation_head_mismatch")
    manifest_target = manifest.get("target_sha") or manifest.get("origin_main_sha")
    if manifest_target != target_sha:
        status["errors"].append("validation_target_mismatch")

    commands = manifest.get("commands")
    if not isinstance(commands, list) or not commands:
        status["errors"].append("validation_commands_missing")
        commands = []
    status["commands_count"] = len(commands)

    matched_categories: set[str] = set()
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            status["errors"].append(f"validation_command_{index}_invalid")
            continue
        category = str(command.get("category", "")).strip()
        cmd = command.get("cmd") or command.get("command")
        cmd_text = str(cmd or "")
        exit_code = command.get("exit_code", command.get("returncode"))
        command_head = command.get("head_sha", manifest.get("head_sha"))
        if not category:
            status["errors"].append(f"validation_command_{index}_category_missing")
        if not cmd:
            status["errors"].append(f"validation_command_{index}_cmd_missing")
        if exit_code != 0:
            status["errors"].append(f"validation_command_{index}_failed")
        if command_head != head_sha:
            status["errors"].append(f"validation_command_{index}_head_mismatch")
        if cmd_text and allowed_prefixes and not any(cmd_text.startswith(prefix) for prefix in allowed_prefixes):
            status["errors"].append(f"validation_command_{index}_not_allowlisted")
        if category and exit_code == 0 and command_head == head_sha:
            matched_categories.add(category)

    status["matched_categories"] = sorted(matched_categories)
    status["valid"] = not status["errors"]
    return status


def run_validation_commands(
    repo: Path,
    command_specs: list[str],
    head_sha: str,
    allowed_prefixes: list[str],
) -> dict:
    status = {
        "commands": [],
        "errors": [],
        "matched_categories": [],
    }
    matched_categories: set[str] = set()
    for index, spec in enumerate(command_specs):
        if "::" not in spec:
            status["errors"].append(f"validation_command_{index}_invalid_spec")
            continue
        category, command = spec.split("::", 1)
        category = category.strip()
        command = command.strip()
        record = {
            "category": category,
            "cmd": command,
            "head_sha": head_sha,
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "allowlisted": False,
        }
        if not category or not command:
            status["errors"].append(f"validation_command_{index}_invalid_spec")
            status["commands"].append(record)
            continue
        if not any(command.startswith(prefix) for prefix in allowed_prefixes):
            status["errors"].append(f"validation_command_{index}_not_allowlisted")
            status["commands"].append(record)
            continue
        record["allowlisted"] = True
        try:
            argv = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            status["errors"].append(f"validation_command_{index}_parse_failed:{exc}")
            status["commands"].append(record)
            continue
        completed = subprocess.run(
            argv,
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        record["exit_code"] = completed.returncode
        record["stdout_tail"] = completed.stdout[-2000:]
        record["stderr_tail"] = completed.stderr[-2000:]
        if completed.returncode != 0:
            status["errors"].append(f"validation_command_{index}_failed")
        else:
            matched_categories.add(category)
        status["commands"].append(record)
    status["matched_categories"] = sorted(matched_categories)
    return status


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether HEAD can be safely pushed to main.")
    parser.add_argument("--repo-root", default=".", help="Repository root or any path inside it.")
    parser.add_argument("--remote", default="origin", help="Remote name.")
    parser.add_argument("--target", default="main", help="Target branch to update.")
    parser.add_argument("--source", default="HEAD", help="Source ref to push.")
    parser.add_argument("--no-fetch", action="store_true", help="Skip git fetch.")
    parser.add_argument("--max-commits", type=int, default=30, help="Maximum commits to show.")
    parser.add_argument("--max-status-lines", type=int, default=12, help="Maximum dirty status lines to preview.")
    parser.add_argument("--skip-diff-check", action="store_true", help="Skip git diff --check for source..target.")
    parser.add_argument("--target-lookback-commits", type=int, default=DEFAULT_TARGET_LOOKBACK_COMMITS, help="Check local paths against recent target changes.")
    parser.add_argument(
        "--validation-manifest",
        help="JSON evidence manifest proving required validation commands passed for current HEAD and target.",
    )
    parser.add_argument(
        "--validation-command",
        action="append",
        default=[],
        help="Run an allowlisted validation command as CATEGORY::COMMAND and bind it to current HEAD.",
    )
    parser.add_argument(
        "--required-validation-categories",
        default=",".join(DEFAULT_REQUIRED_VALIDATION_CATEGORIES),
        help="Comma-separated validation categories required in the manifest.",
    )
    parser.add_argument(
        "--allowed-validation-prefix",
        action="append",
        default=list(DEFAULT_ALLOWED_VALIDATION_PREFIXES),
        help="Allowed command prefix for validation manifest entries.",
    )
    parser.add_argument(
        "--acknowledge-recent-target-overlap",
        action="store_true",
        help="Acknowledge that local edits touch files changed in recent target commits. "
        "Downgrades recent_target_overlap to a warning ONLY when every other safety gate passes "
        "and validation is bound to the current HEAD. This is part of R7 human review, not a blanket override.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo = find_repo_root(Path(args.repo_root).resolve())
    target_ref = f"{args.remote}/{args.target}"
    target_full_ref = f"refs/remotes/{args.remote}/{args.target}"
    branch = current_branch(repo)
    branch_upstream = upstream_ref(repo)
    required_validation_categories = split_csv(args.required_validation_categories)
    validation_policy_downgraded = any(
        category not in required_validation_categories
        for category in DEFAULT_REQUIRED_VALIDATION_CATEGORIES
    )

    fetch_result = None
    if not args.no_fetch:
        fetch_result = fetch_remote_tracking(repo, args.remote, args.target)

    target_exists = ref_exists(repo, target_full_ref)
    source_exists = ref_exists(repo, args.source)
    source_type = object_type(repo, args.source) if source_exists else None
    source_is_commit = source_type == "commit"
    head_sha = full_ref(repo, "HEAD")
    source_sha = full_ref(repo, args.source)
    target_sha = full_ref(repo, target_full_ref)
    source_is_head = source_sha == head_sha
    target_is_main = args.target in MAIN_BRANCHES
    replace_ref_list = replace_refs(repo)
    ambiguous_target_refs = matching_refs(repo, target_ref)
    status_text = porcelain_status(repo)
    status_summary = summarize_status(status_text, args.max_status_lines)
    clean = status_text == ""
    main_is_ancestor = target_exists and source_exists and source_is_commit and is_ancestor(repo, target_full_ref, args.source)
    no_replace_main_is_ancestor = (
        target_exists
        and source_exists
        and source_is_commit
        and is_ancestor_no_replace(repo, target_full_ref, args.source)
    )
    inspectable_range = target_exists and source_exists and source_is_commit and main_is_ancestor
    commits = list_commits(repo, target_full_ref, args.source, args.max_commits) if inspectable_range else []
    all_commit_infos = list_commit_infos(repo, target_full_ref, args.source) if inspectable_range else []
    merge_commits = list_merge_commits(repo, target_full_ref, args.source) if inspectable_range else []
    stat = diff_stat(repo, target_full_ref, args.source) if inspectable_range else ""
    metrics = diff_metrics(repo, target_full_ref, args.source) if inspectable_range else DiffMetrics(0, 0, 0, 0)
    name_status = name_status_entries(repo, target_full_ref, args.source) if inspectable_range else []
    source_paths = changed_paths(repo, target_full_ref, args.source) if inspectable_range else []
    target_recent_paths = recent_changed_paths(repo, target_full_ref, args.target_lookback_commits) if target_exists else []
    recent_target_overlap_paths = sorted(set(source_paths).intersection(target_recent_paths))
    renamed_paths = [entry.__dict__ for entry in name_status if entry.status.startswith("R")]
    deleted_paths = [entry.path for entry in name_status if entry.status.startswith("D")]
    path_case_collisions = case_collisions(source_paths)
    operation_state = git_operation_state(repo)
    hidden_flags = hidden_index_flags(repo)
    has_commits_to_main = bool(commits)
    extra_validation_prefixes = [
        prefix for prefix in args.allowed_validation_prefix if prefix not in DEFAULT_ALLOWED_VALIDATION_PREFIXES
    ]
    validation_status = load_validation_manifest(
        args.validation_manifest,
        repo,
        head_sha,
        target_sha,
        required_validation_categories,
        args.allowed_validation_prefix,
    )
    command_validation_status = run_validation_commands(
        repo,
        args.validation_command,
        head_sha,
        args.allowed_validation_prefix,
    )
    combined_matched_categories = sorted(
        set(validation_status.get("matched_categories", []))
        | set(command_validation_status.get("matched_categories", []))
    )
    validation_status["commands"] = validation_status.get("commands", []) + command_validation_status["commands"]
    validation_status["commands_count"] = len(validation_status["commands"])
    validation_status["errors"].extend(command_validation_status["errors"])
    validation_status["matched_categories"] = combined_matched_categories
    if has_commits_to_main and not args.validation_manifest and not args.validation_command:
        validation_status["errors"].append("validation_evidence_missing")
    for category in required_validation_categories:
        if has_commits_to_main and category not in combined_matched_categories:
            validation_status["errors"].append(f"validation_category_missing:{category}")
    validation_status["valid"] = bool(has_commits_to_main) and not validation_status["errors"]
    diff_check_result = None
    if (
        not args.skip_diff_check
        and target_exists
        and source_exists
        and main_is_ancestor
        and has_commits_to_main
    ):
        diff_check_result = diff_check(repo, target_ref, args.source)

    branch_remote_ref = None
    branch_remote_full_ref = None
    ambiguous_branch_remote_refs: list[str] = []
    branch_remote_exists = None
    branch_remote_tracking_available = None
    branch_remote_comparable = None
    branch_remote_ancestor = None
    branch_remote_divergence = None
    branch_remote_probe_returncode = None
    branch_fetch_returncode = None
    branch_tracks_target = branch_upstream == target_ref
    if branch not in {"HEAD", args.target}:
        branch_remote_ref = f"{args.remote}/{branch}"
        branch_remote_full_ref = f"refs/remotes/{args.remote}/{branch}"
        ambiguous_branch_remote_refs = matching_refs(repo, branch_remote_ref)
        if args.no_fetch or (fetch_result and fetch_result.returncode != 0):
            branch_remote_exists = ref_exists(repo, branch_remote_full_ref)
            branch_remote_comparable = branch_remote_exists
        else:
            branch_probe = remote_head_probe(repo, args.remote, branch)
            branch_remote_probe_returncode = branch_probe.returncode
            if branch_probe.returncode == 0 and branch_probe.stdout:
                branch_remote_exists = True
                branch_fetch = fetch_remote_tracking(repo, args.remote, branch)
                branch_fetch_returncode = branch_fetch.returncode
            elif branch_probe.returncode == 2:
                branch_remote_exists = False
            else:
                branch_remote_exists = None
        branch_remote_tracking_available = ref_exists(repo, branch_remote_full_ref)
        if branch_remote_comparable is None:
            branch_remote_comparable = (
                branch_remote_exists is True
                and branch_remote_tracking_available
                and branch_fetch_returncode in {None, 0}
            )
        if branch_remote_exists and branch_remote_comparable:
            branch_remote_ancestor = is_ancestor(repo, branch_remote_full_ref, args.source)
            branch_remote_divergence = rev_list_divergence(repo, branch_remote_full_ref, args.source)

    blockers: list[str] = []
    warnings: list[str] = []
    next_actions: list[str] = []
    if operation_state["in_progress"]:
        blockers.append("git_operation_in_progress")
        next_actions.append("Finish or abort the in-progress git operation, then rerun validation and preflight.")
    if hidden_flags:
        blockers.append("hidden_index_flags")
        next_actions.append("Clear assume-unchanged/skip-worktree flags before validating push safety.")
    if not target_is_main:
        blockers.append("target_not_main")
        next_actions.append("perfect-push only approves main/master targets; use a different workflow for other targets.")
    if branch == "HEAD":
        blockers.append("detached_head")
        next_actions.append("Check out a named task branch before running perfect-push.")
    if source_exists and not source_is_commit:
        blockers.append("source_not_commit")
        next_actions.append("Use HEAD as source; trees/blobs/tags are not accepted for main push preflight.")
    if source_exists and source_is_commit and not source_is_head:
        blockers.append("source_not_head")
        next_actions.append("Rerun with --source HEAD; perfect-push only approves the checked-out HEAD.")
    if len(ambiguous_target_refs) > 1:
        blockers.append("ambiguous_target_ref")
        next_actions.append(f"Resolve ambiguous ref {target_ref}; target must be {target_full_ref}.")
    if replace_ref_list:
        blockers.append("replace_refs_present")
        next_actions.append("Remove or disable git replace refs before evaluating push safety.")
    if main_is_ancestor and not no_replace_main_is_ancestor:
        blockers.append("replace_refs_affect_ancestry")
        next_actions.append("Ancestry differs with GIT_NO_REPLACE_OBJECTS=1; remove replace refs before pushing main.")
    if validation_policy_downgraded and has_commits_to_main:
        blockers.append("validation_policy_downgraded")
        next_actions.append("Static and integration validation categories are both required for main push.")
    if args.target_lookback_commits <= 0 and has_commits_to_main:
        blockers.append("target_lookback_disabled")
        next_actions.append("Rerun with a positive --target-lookback-commits value.")
    if args.no_fetch:
        blockers.append("fetch_skipped")
        next_actions.append(f"Rerun without --no-fetch so {target_ref} and the feature branch are fresh.")
    if fetch_result and fetch_result.returncode != 0:
        blockers.append("fetch_failed")
        next_actions.append(f"Fix fetch failure, then rerun: git fetch {args.remote} {args.target}")
    if not target_exists:
        blockers.append("target_missing")
        next_actions.append(f"Check remote/target spelling or fetch refs: git fetch {args.remote} {args.target}")
    if not source_exists:
        blockers.append("source_missing")
        next_actions.append(f"Check source ref spelling: git rev-parse --verify {args.source}")
    if not clean:
        blockers.append("dirty_worktree")
        next_actions.append("Review dirty files: git status --short --branch")
        next_actions.append("If these changes should enter main, stage the exact files and commit them first.")
        next_actions.append("If they should not enter main, move them to a separate worktree or stash intentionally.")
    if target_exists and source_exists and source_is_commit and not main_is_ancestor:
        blockers.append("target_not_ancestor")
        next_actions.append(f"Rebase onto latest target: git rebase {target_full_ref}")
        next_actions.append("After rebase/conflict resolution, rerun validation and this preflight.")
    if args.skip_diff_check and has_commits_to_main:
        blockers.append("diff_check_skipped")
        next_actions.append("Rerun without --skip-diff-check; skipped patch checks cannot approve main push.")
    if diff_check_result and diff_check_result.returncode != 0:
        blockers.append("diff_check_failed")
        next_actions.append(f"Fix whitespace/conflict-marker issues reported by: git diff --check {target_full_ref}..{args.source}")
        next_actions.append("Commit or amend the fixes, then rerun validation and this preflight.")
    if has_commits_to_main and not source_paths:
        blockers.append("empty_effective_diff")
        next_actions.append("Commit range has no effective file diff; do not push main for an empty result.")
    if merge_commits:
        blockers.append("merge_commits_in_range")
        next_actions.append("Rebase instead of merging target into the task branch, then rerun conflict validation.")
    if metrics.binary_files > 0:
        blockers.append("binary_files_changed")
        next_actions.append("Binary diffs cannot be inspected for dropped conflict sides; review them before main push.")
    if deleted_paths:
        blockers.append("deleted_files_in_range")
        next_actions.append("Deleted files can drop target-side changes; review deletions before main push.")
    if renamed_paths:
        blockers.append("renamed_files_in_range")
        next_actions.append("Renamed files can hide target-side edits; review old/new paths before main push.")
    if path_case_collisions:
        blockers.append("case_colliding_paths")
        next_actions.append("Case-colliding paths are unsafe on Windows/macOS; rename before pushing main.")
    if recent_target_overlap_paths:
        blockers.append("recent_target_overlap")
        next_actions.append(
            f"Local changes overlap files changed in the last {args.target_lookback_commits} target commits; "
            "review compatibility and rerun integration validation."
        )
    if validation_status["errors"]:
        blockers.extend(str(error) for error in validation_status["errors"])
        next_actions.append(
            "Create or update a validation manifest for the current HEAD with passing static and integration checks."
        )
    if not blockers and not has_commits_to_main:
        next_actions.append("No commits are waiting for main; do not push main for this branch.")

    if branch_tracks_target and branch not in MAIN_BRANCHES:
        warnings.append(
            f"Current branch tracks {target_ref}; branch push logic uses {args.remote}/{branch}, not the upstream target branch."
        )
    if len(ambiguous_branch_remote_refs) > 1:
        blockers.append("ambiguous_feature_branch_remote_ref")
        next_actions.append(f"Resolve ambiguous feature branch ref {branch_remote_ref}; use {branch_remote_full_ref}.")
    if branch_remote_probe_returncode not in {None, 0, 2}:
        blockers.append("feature_branch_remote_unknown")
        next_actions.append(f"Inspect remote feature branch manually: git ls-remote --heads {args.remote} {branch}")
        warnings.append(
            f"Could not query remote feature branch {args.remote}/{branch}; branch push recommendation uses local refs only."
        )
    if branch_fetch_returncode not in {None, 0}:
        blockers.append("feature_branch_fetch_failed")
        next_actions.append(f"Refresh the remote feature branch before pushing main: git fetch {args.remote} {branch}")
        warnings.append(
            f"Could not refresh remote-tracking ref {branch_remote_ref}; inspect branch history before pushing it."
        )
    if branch_remote_divergence:
        remote_only, local_only = branch_remote_divergence
        if remote_only > 0:
            blockers.append("feature_branch_has_remote_only_commits")
            if local_only == 0:
                next_actions.append(
                    f"Local branch is behind {branch_remote_ref}; rebase or merge the remote feature branch before pushing main."
                )
            else:
                next_actions.append(
                    f"{branch_remote_ref} diverged; align the feature branch first, then rerun this preflight before pushing main."
                )

    branch_push = None
    branch_push_note = None
    if branch != "HEAD" and branch != args.target:
        if branch_remote_exists is False:
            branch_push = f"git push -u {args.remote} {branch}"
            branch_push_note = f"remote branch missing: {branch_remote_ref}; -u will set the feature branch upstream."
        elif branch_remote_exists is None:
            branch_push_note = (
                f"could not determine whether remote branch {args.remote}/{branch} exists; inspect it manually."
            )
            warnings.append(branch_push_note)
        elif branch_remote_exists is True and not branch_remote_comparable:
            branch_push_note = (
                f"remote branch exists but {branch_remote_ref} could not be refreshed locally; "
                "fetch or inspect the remote feature branch before pushing it."
            )
            warnings.append(branch_push_note)
        elif branch_remote_ancestor is True:
            branch_push = f"git push {args.remote} {branch}"
            branch_push_note = f"{branch_remote_ref} is an ancestor of {args.source}; ordinary branch push is safe."
        elif branch_remote_divergence:
            remote_only, local_only = branch_remote_divergence
            if remote_only > 0 and local_only == 0:
                branch_push_note = (
                    f"local branch is behind {branch_remote_ref}; fetch/rebase the remote feature branch before pushing it."
                )
                warnings.append(branch_push_note)
            else:
                branch_push = f"git push --force-with-lease {args.remote} {branch}"
                branch_push_note = (
                    f"{branch_remote_ref} diverged from {args.source}; use force-with-lease only after confirming "
                    "the remote-only commits are the pre-rebase version of this same task."
                )
                warnings.append(branch_push_note)
        else:
            branch_push_note = f"could not determine whether {branch_remote_ref} is safe to update; inspect it manually."
            warnings.append(branch_push_note)

    overlap_acknowledged = False
    if (
        args.acknowledge_recent_target_overlap
        and blockers == ["recent_target_overlap"]
        and bool(validation_status.get("valid"))
        and main_is_ancestor
        and clean
        and not operation_state.get("in_progress")
    ):
        blockers.remove("recent_target_overlap")
        warnings.append(
            "recent_target_overlap acknowledged after R7 review: local edits touch files changed in "
            "recent target commits, but the branch is rebased onto target, validation is bound to the "
            "current HEAD, and every other safety gate passes."
        )
        overlap_acknowledged = True

    can_push_main = not blockers and has_commits_to_main
    post_push_verify_commands = [
        f"git fetch {args.remote} +refs/heads/{args.target}:refs/remotes/{args.remote}/{args.target}",
        "git rev-parse --short HEAD",
        f"git rev-parse --short {target_full_ref}",
        "git status --short --branch",
    ]
    summary = {
        "repo": str(repo),
        "branch": branch,
        "source": args.source,
        "target_ref": target_ref,
        "target_full_ref": target_full_ref,
        "head": short_ref(repo, "HEAD"),
        "head_sha": head_sha,
        "source_sha": source_sha,
        "target": short_ref(repo, target_full_ref),
        "target_sha": target_sha,
        "target_is_main": target_is_main,
        "source_type": source_type,
        "source_is_commit": source_is_commit,
        "source_is_head": source_is_head,
        "ambiguous_target_refs": ambiguous_target_refs,
        "replace_refs": replace_ref_list,
        "no_replace_main_is_ancestor": no_replace_main_is_ancestor,
        "clean": clean,
        "status_summary": status_summary.__dict__,
        "target_exists": target_exists,
        "source_exists": source_exists,
        "main_is_ancestor": main_is_ancestor,
        "has_commits_to_main": has_commits_to_main,
        "can_push_main": can_push_main,
        "safety_scope": {
            "goal": "git_update_push_conflict_no_local_loss_no_remote_overwrite",
            "target_lookback_commits": args.target_lookback_commits,
            "required_validation_categories": required_validation_categories,
            "validation_policy_downgraded": validation_policy_downgraded,
            "extra_validation_prefixes": extra_validation_prefixes,
            "overlap_acknowledged": overlap_acknowledged,
        },
        "commit_range": {
            "count": len(all_commit_infos),
            "commits": [info.__dict__ for info in all_commit_infos],
            "merge_commits": merge_commits,
        },
        "diff_metrics": {
            **metrics.__dict__,
            "total_churn": metrics.insertions + metrics.deletions,
            "renames": renamed_paths,
            "deleted_paths": deleted_paths,
            "case_collisions": path_case_collisions,
            "changed_paths": source_paths,
            "recent_target_overlap_paths": recent_target_overlap_paths,
        },
        "git_operation_state": operation_state,
        "hidden_index_flags": hidden_flags,
        "validation_status": validation_status,
        "branch_upstream": branch_upstream,
        "branch_tracks_target": branch_tracks_target,
        "branch_remote_ref": branch_remote_ref,
        "branch_remote_full_ref": branch_remote_full_ref,
        "ambiguous_branch_remote_refs": ambiguous_branch_remote_refs,
        "branch_remote_exists": branch_remote_exists,
        "branch_remote_tracking_available": branch_remote_tracking_available,
        "branch_remote_comparable": branch_remote_comparable,
        "branch_remote_ancestor": branch_remote_ancestor,
        "branch_remote_divergence": branch_remote_divergence,
        "branch_remote_probe_returncode": branch_remote_probe_returncode,
        "branch_fetch_returncode": branch_fetch_returncode,
        "branch_push": branch_push,
        "branch_push_note": branch_push_note,
        "main_push": f"git push {args.remote} HEAD:{args.target}" if can_push_main else None,
        "main_push_argv": ["git", "push", args.remote, f"HEAD:{args.target}"] if can_push_main else None,
        "fetch_returncode": fetch_result.returncode if fetch_result else None,
        "diff_check_returncode": diff_check_result.returncode if diff_check_result else None,
        "diff_check_output": (
            (diff_check_result.stdout + "\n" + diff_check_result.stderr).strip()
            if diff_check_result
            else ""
        ),
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions,
        "post_push_verify_commands": post_push_verify_commands,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if can_push_main else 2

    print_section("Perfect Push Preflight")
    print(f"repo: {repo}")
    print(f"branch: {branch}")
    print(f"source: {args.source} ({summary['source_sha'][:8] if summary['source_sha'] != '<missing>' else '<missing>'})")
    print(f"head: {summary['head']} ({summary['head_sha'][:8] if summary['head_sha'] != '<missing>' else '<missing>'})")
    print(f"target: {target_full_ref} ({summary['target']})")
    print(f"worktree clean: {'yes' if clean else 'no'}")
    if fetch_result:
        print(f"fetch {args.remote} {args.target}: {'ok' if fetch_result.returncode == 0 else 'failed'}")
        if fetch_result.returncode != 0:
            print(fetch_result.stderr or fetch_result.stdout)

    print_section("Fast Path Decision")
    if blockers:
        print("BLOCKED: " + ", ".join(blockers))
    elif not has_commits_to_main:
        print(f"OK: {args.source} already matches {target_ref}; nothing to push to main.")
    else:
        print(f"OK: {target_ref} can be fast-forwarded by ordinary push after validation.")
    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"- {warning}")

    if not clean:
        print_section("Dirty Worktree")
        print(
            "staged={staged} unstaged={unstaged} untracked={untracked} conflicts={conflicts}".format(
                **status_summary.__dict__
            )
        )
        if status_summary.preview:
            print("preview:")
            for line in status_summary.preview:
                print(f"  {line}")

    print_section("Main Safety")
    if fetch_result and fetch_result.returncode != 0:
        print(f"BLOCK: fetch failed for {args.remote} {args.target}; target information may be stale.")
    elif not target_exists:
        print(f"BLOCK: target ref is missing: {target_ref}")
    elif not source_exists:
        print(f"BLOCK: source ref is missing: {args.source}")
    elif not clean:
        print("BLOCK: worktree is not clean. Commit or intentionally set aside local changes first.")
    elif not main_is_ancestor:
        print(f"BLOCK: {target_ref} is NOT an ancestor of {args.source}.")
        print(f"Run: git rebase {target_ref}")
        print("Resolve conflicts by preserving both local and target-branch intent, then rerun validation and this skill.")
    else:
        print(f"OK: {target_full_ref} is an ancestor of {args.source}; main can be fast-forwarded by ordinary push.")

    print_section("Safety Gates")
    print(f"source_is_head: {'yes' if source_is_head else 'no'}")
    print(f"target_is_main: {'yes' if target_is_main else 'no'}")
    print(f"commit_count: {len(all_commit_infos)}")
    print(
        "diff_metrics: files={files} insertions={insertions} deletions={deletions} binary={binary_files}".format(
            **metrics.__dict__
        )
    )
    if merge_commits:
        print("merge commits:")
        print_command_list(merge_commits)
    if recent_target_overlap_paths:
        print("recent target overlap:")
        print_command_list(recent_target_overlap_paths[:20])

    print_section("Validation Evidence")
    print(f"manifest: {validation_status.get('path') or '<missing>'}")
    print(f"valid: {'yes' if validation_status.get('valid') else 'no'}")
    if validation_status.get("errors"):
        print("errors:")
        print_command_list([str(error) for error in validation_status["errors"]])

    print_section("Diff Check")
    if diff_check_result is None:
        if args.skip_diff_check:
            print("skipped by --skip-diff-check")
        elif not has_commits_to_main:
            print("skipped: no commits to push to main")
        elif not main_is_ancestor:
            print("skipped: rebase onto target first, then rerun")
        else:
            print("skipped: target/source missing")
    elif diff_check_result.returncode == 0:
        print(f"OK: git diff --check {target_ref}..{args.source}")
    else:
        print(f"BLOCK: git diff --check {target_ref}..{args.source} failed")
        print((diff_check_result.stdout + "\n" + diff_check_result.stderr).strip())

    print_section("Commits To Main")
    if commits:
        for line in commits:
            print(line)
        if len(commits) == args.max_commits:
            print(f"... truncated at {args.max_commits} commits")
    else:
        print("<none>")

    print_section("Diff Stat")
    print(stat or "<empty>")

    print_section("Recommended Commands")
    print("Run project-specific validation after every rebase or conflict resolution.")
    if branch_push:
        print(f"push branch: {branch_push}")
        if branch_push_note:
            print(f"branch note: {branch_push_note}")
    elif branch == args.target:
        print("push branch: current branch is target branch; do not force push main.")
    else:
        print(branch_push_note or "push branch: no branch push recommendation for detached HEAD.")
    if can_push_main:
        print(f"push main: git push {args.remote} HEAD:{args.target}")
        print("post-push verify:")
        print_command_list(post_push_verify_commands)
    else:
        print("push main: blocked until Main Safety is OK.")

    print_section("Next Actions")
    if next_actions:
        print_command_list(next_actions)
    else:
        print("- Validate the task-specific behavior.")
        if branch_push:
            print(f"- {branch_push}")
        if can_push_main:
            print(f"- git push {args.remote} HEAD:{args.target}")
            for command in post_push_verify_commands:
                print(f"- {command}")

    print_section("Rules")
    print("- Never use git pull in this workflow.")
    print("- Never force push main.")
    print("- Use --force-with-lease only for a rebased feature branch, not for main.")
    print("- If main push is rejected, fetch, rebase, validate, rerun this skill, then retry ordinary push.")
    return 0 if can_push_main else 2


if __name__ == "__main__":
    raise SystemExit(main())
