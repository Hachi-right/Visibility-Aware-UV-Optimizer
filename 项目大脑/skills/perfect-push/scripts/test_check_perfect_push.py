#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


SCRIPT = Path(__file__).with_name("check_perfect_push.py")


def run(cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def preflight(work: Path, *extra_args: str) -> tuple[int, dict]:
    result = subprocess.run(
        ["python", str(SCRIPT), "--repo-root", str(work), "--json", *extra_args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"bad json rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        ) from exc
    return result.returncode, data


def validation_args() -> list[str]:
    return [
        "--validation-command",
        "static::git diff --check refs/remotes/origin/main..HEAD",
        "--validation-command",
        "integration::python -m py_compile app.py",
    ]


def make_repo() -> tuple[Path, Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="perfect-push-suite-"))
    origin = temp_dir / "origin.git"
    work = temp_dir / "work"
    run(temp_dir, "git", "init", "--bare", str(origin))
    run(temp_dir, "git", "clone", str(origin), str(work))
    run(work, "git", "config", "user.email", "codex@example.invalid")
    run(work, "git", "config", "user.name", "Codex Test")
    (work / "README.md").write_text("base\n", encoding="utf-8")
    (work / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    run(work, "git", "add", "README.md", "app.py")
    run(work, "git", "commit", "-m", "base")
    run(work, "git", "push", "-u", "origin", "HEAD:main")
    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "checkout", "-b", "task/test", "origin/main")
    return temp_dir, origin, work


def clone_writer(temp_dir: Path, origin: Path, name: str = "writer") -> Path:
    clone = temp_dir / name
    run(temp_dir, "git", "clone", str(origin), str(clone))
    run(clone, "git", "config", "user.email", "codex@example.invalid")
    run(clone, "git", "config", "user.name", "Codex Test")
    return clone


def add_commit(work: Path, filename: str = "feature.py", content: str = "FEATURE = 1\n", message: str = "feature") -> None:
    target = work / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run(work, "git", "add", filename)
    run(work, "git", "commit", "-m", message)


def assert_contains(value: str, items: list[str]) -> None:
    if value not in items:
        raise AssertionError(f"expected {value!r} in {items!r}")


def assert_blocked(data: dict, blocker: str | None = None) -> None:
    assert data["can_push_main"] is False, data
    assert data["main_push"] is None, data
    if blocker:
        assert_contains(blocker, data["blockers"])


def clean_missing_remote_branch(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    rc, data = preflight(work, *validation_args())
    assert rc == 0, data
    assert data["can_push_main"] is True, data
    assert data["validation_status"]["valid"] is True, data
    assert data["branch_remote_exists"] is False, data
    assert data["branch_push"] == "git push -u origin task/test", data
    assert data["main_push"] == "git push origin HEAD:main", data


def validation_evidence_missing(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    rc, data = preflight(work)
    assert rc == 2, data
    assert_blocked(data, "validation_evidence_missing")
    assert_contains("validation_category_missing:static", data["blockers"])
    assert_contains("validation_category_missing:integration", data["blockers"])


def remote_exists_tracking_ref_deleted(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    run(work, "git", "push", "-u", "origin", "task/test")
    run(work, "git", "update-ref", "-d", "refs/remotes/origin/task/test")
    rc, data = preflight(work, *validation_args())
    assert rc == 0, data
    assert data["branch_remote_exists"] is True, data
    assert data["branch_remote_tracking_available"] is True, data
    assert data["branch_remote_comparable"] is True, data
    assert data["branch_push"] == "git push origin task/test", data


def dirty_worktree(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    (work / "README.md").write_text("base\ndirty\n", encoding="utf-8")
    (work / "untracked.txt").write_text("new\n", encoding="utf-8")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "dirty_worktree")
    assert data["status_summary"]["unstaged"] >= 1, data
    assert data["status_summary"]["untracked"] >= 1, data


def hidden_local_change_blocks(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    run(work, "git", "update-index", "--assume-unchanged", "README.md")
    (work / "README.md").write_text("base\nhidden local edit\n", encoding="utf-8")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "hidden_index_flags")
    assert data["hidden_index_flags"], data


def target_not_ancestor(temp_dir: Path, origin: Path, work: Path) -> None:
    add_commit(work)
    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "main.txt", "remote main\n", "advance main")
    run(other, "git", "push", "origin", "main")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "target_not_ancestor")
    assert data["diff_metrics"]["files"] == 0, data


def diff_check_failed(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work, "bad.txt", "bad   \n", "bad whitespace")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "diff_check_failed")
    assert "bad.txt" in data["diff_check_output"], data


def skip_diff_check_blocks(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    rc, data = preflight(work, "--skip-diff-check", *validation_args())
    assert rc == 2, data
    assert_blocked(data, "diff_check_skipped")


def no_fetch_blocks(temp_dir: Path, origin: Path, work: Path) -> None:
    add_commit(work)
    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "remote_after_fetch.py", "REMOTE = 1\n", "advance main")
    run(other, "git", "push", "origin", "main")
    rc, data = preflight(work, "--no-fetch", *validation_args())
    assert rc == 2, data
    assert_blocked(data, "fetch_skipped")


def validation_command_not_allowlisted(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    rc, data = preflight(
        work,
        "--validation-command",
        "static::echo ok",
        "--validation-command",
        "integration::python -m py_compile app.py",
    )
    assert rc == 2, data
    assert_blocked(data, "validation_command_0_not_allowlisted")


def diverged_feature_branch(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work, "feature.py", "REMOTE_OLD = 1\n", "remote old")
    run(work, "git", "push", "-u", "origin", "task/test")
    run(work, "git", "reset", "--hard", "origin/main")
    add_commit(work, "feature.py", "LOCAL_REWRITE = 1\n", "local rewrite")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "feature_branch_has_remote_only_commits")
    assert data["branch_remote_divergence"] == [1, 1], data
    assert data["branch_push"] == "git push --force-with-lease origin task/test", data


def remote_feature_branch_ahead_only(temp_dir: Path, origin: Path, work: Path) -> None:
    add_commit(work, "feature.py", "LOCAL = 1\n", "local")
    run(work, "git", "push", "-u", "origin", "task/test")
    other = clone_writer(temp_dir, origin, "feature_writer")
    run(other, "git", "checkout", "task/test")
    add_commit(other, "remote_extra.py", "REMOTE_EXTRA = 1\n", "remote extra")
    run(other, "git", "push", "origin", "task/test")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "feature_branch_has_remote_only_commits")
    assert data["branch_push"] is None, data


def no_commits_to_main(_temp_dir: Path, _origin: Path, work: Path) -> None:
    rc, data = preflight(work)
    assert rc == 2, data
    assert data["has_commits_to_main"] is False, data
    assert data["can_push_main"] is False, data
    assert data["main_push"] is None, data


def ambiguous_origin_main_ref_blocks(temp_dir: Path, origin: Path, work: Path) -> None:
    add_commit(work)
    run(work, "git", "branch", "origin/main", "origin/main")
    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "remote.py", "REMOTE = 1\n", "advance main")
    run(other, "git", "push", "origin", "main")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "ambiguous_target_ref")
    assert "refs/heads/origin/main" in data["ambiguous_target_refs"], data
    assert data["target_full_ref"] == "refs/remotes/origin/main", data


def source_must_be_head(_temp_dir: Path, _origin: Path, work: Path) -> None:
    run(work, "git", "checkout", "-b", "sourcebranch")
    add_commit(work, "source.py", "SOURCE = 1\n", "source")
    run(work, "git", "checkout", "task/test")
    add_commit(work, "head.py", "HEAD_VALUE = 1\n", "head")
    rc, data = preflight(work, "--source", "sourcebranch", *validation_args())
    assert rc == 2, data
    assert_blocked(data, "source_not_head")
    assert data["source_is_head"] is False, data


def detached_head_blocks(_temp_dir: Path, _origin: Path, work: Path) -> None:
    run(work, "git", "checkout", "--detach", "origin/main")
    add_commit(work, "detached.py", "DETACHED = 1\n", "detached")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "detached_head")
    assert data["branch"] == "HEAD", data


def replace_refs_block(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    run(work, "git", "replace", "HEAD", "origin/main")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "replace_refs_present")
    assert data["replace_refs"], data


def feature_branch_remote_ref_shadow(temp_dir: Path, origin: Path, work: Path) -> None:
    add_commit(work, "feature.py", "LOCAL = 1\n", "local")
    run(work, "git", "push", "-u", "origin", "task/test")
    run(work, "git", "branch", "origin/task/test", "HEAD")
    other = clone_writer(temp_dir, origin, "feature_writer")
    run(other, "git", "checkout", "task/test")
    add_commit(other, "remote_shadow.py", "REMOTE = 1\n", "remote")
    run(other, "git", "push", "origin", "task/test")
    add_commit(work, "local_shadow.py", "LOCAL2 = 1\n", "local 2")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "ambiguous_feature_branch_remote_ref")
    assert "refs/heads/origin/task/test" in data["ambiguous_branch_remote_refs"], data
    assert data["branch_remote_full_ref"] == "refs/remotes/origin/task/test", data


def validation_manifest_in_git_dir(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work)
    manifest = work / ".git" / "fake-validation.json"
    head = run(work, "git", "rev-parse", "HEAD").stdout.strip()
    target = run(work, "git", "rev-parse", "refs/remotes/origin/main").stdout.strip()
    manifest.write_text(
        json.dumps(
            {
                "head_sha": head,
                "target_sha": target,
                "commands": [
                    {"category": "static", "cmd": "git diff --check refs/remotes/origin/main..HEAD", "exit_code": 0, "head_sha": head},
                    {"category": "integration", "cmd": "python -m py_compile app.py", "exit_code": 0, "head_sha": head},
                ],
            }
        ),
        encoding="utf-8",
    )
    rc, data = preflight(work, "--validation-manifest", str(manifest))
    assert rc == 2, data
    assert_blocked(data, "validation_manifest_in_git_dir")
    assert_contains("validation_manifest_in_git_dir", data["validation_status"]["errors"])


def merge_commit_blocks(temp_dir: Path, origin: Path, work: Path) -> None:
    add_commit(work, "feature.py", "FEATURE = 1\n", "feature")
    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "main_change.py", "MAIN = 1\n", "advance main")
    run(other, "git", "push", "origin", "main")
    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "merge", "--no-ff", "refs/remotes/origin/main", "-m", "merge origin main")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert data["main_is_ancestor"] is True, data
    assert_blocked(data, "merge_commits_in_range")


def rebase_in_progress_blocks(temp_dir: Path, origin: Path, work: Path) -> None:
    (work / "during_rebase.txt").write_text("base\n", encoding="utf-8")
    run(work, "git", "add", "during_rebase.txt")
    run(work, "git", "commit", "-m", "add rebase target")
    run(work, "git", "push", "origin", "HEAD:main")
    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "checkout", "-B", "task/test", "origin/main")
    add_commit(work, "during_rebase.txt", "feature side\n", "feature edits rebase target")

    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "during_rebase.txt", "main side\n", "main edits rebase target")
    run(other, "git", "push", "origin", "main")

    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "rebase", "refs/remotes/origin/main", check=False)
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "git_operation_in_progress")
    assert data["git_operation_state"]["in_progress"] is True, data


def same_file_conflict_drops_main_side(temp_dir: Path, origin: Path, work: Path) -> None:
    (work / "shared.txt").write_text("base\n", encoding="utf-8")
    run(work, "git", "add", "shared.txt")
    run(work, "git", "commit", "-m", "add shared")
    run(work, "git", "push", "origin", "HEAD:main")
    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "checkout", "-B", "task/test", "origin/main")
    add_commit(work, "shared.txt", "feature side\n", "feature shared")

    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "shared.txt", "main mechanism\n", "main shared")
    run(other, "git", "push", "origin", "main")

    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "rebase", "refs/remotes/origin/main", check=False)
    (work / "shared.txt").write_text("feature side\n", encoding="utf-8")
    run(work, "git", "add", "shared.txt")
    run(work, "git", "rebase", "--continue", env={**__import__("os").environ, "GIT_EDITOR": "true"})
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert data["main_is_ancestor"] is True, data
    assert_blocked(data, "recent_target_overlap")
    assert "shared.txt" in data["diff_metrics"]["recent_target_overlap_paths"], data


def delete_remote_changed_file_blocks(temp_dir: Path, origin: Path, work: Path) -> None:
    (work / "shared_delete.txt").write_text("base\n", encoding="utf-8")
    run(work, "git", "add", "shared_delete.txt")
    run(work, "git", "commit", "-m", "add shared delete target")
    run(work, "git", "push", "origin", "HEAD:main")
    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "checkout", "-B", "task/test", "origin/main")
    run(work, "git", "rm", "shared_delete.txt")
    run(work, "git", "commit", "-m", "delete shared file")

    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "shared_delete.txt", "main changed\n", "main modifies shared")
    run(other, "git", "push", "origin", "main")

    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "rebase", "refs/remotes/origin/main", check=False)
    run(work, "git", "rm", "shared_delete.txt")
    run(work, "git", "rebase", "--continue", env={**__import__("os").environ, "GIT_EDITOR": "true"})
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "deleted_files_in_range")
    assert_contains("recent_target_overlap", data["blockers"])
    assert "shared_delete.txt" in data["diff_metrics"]["deleted_paths"], data


def rename_blocks(_temp_dir: Path, _origin: Path, work: Path) -> None:
    (work / "old_name.txt").write_text("base\n", encoding="utf-8")
    run(work, "git", "add", "old_name.txt")
    run(work, "git", "commit", "-m", "add rename target")
    run(work, "git", "push", "origin", "HEAD:main")
    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "checkout", "-B", "task/test", "origin/main")
    run(work, "git", "mv", "old_name.txt", "new_name.txt")
    run(work, "git", "commit", "-m", "rename file")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "renamed_files_in_range")
    assert data["diff_metrics"]["renames"], data


def case_collision_blocks(_temp_dir: Path, _origin: Path, work: Path) -> None:
    add_commit(work, "Foo.py", "A = 1\n", "add Foo")
    # Re-run with input is awkward through the shared helper; use subprocess directly for this index-only path.
    completed = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=work,
        input="B = 1\n",
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    blob_oid = completed.stdout.strip()
    run(work, "git", "update-index", "--add", "--cacheinfo", "100644", blob_oid, "foo.py")
    run(work, "git", "commit", "-m", "add lowercase foo")
    rc, data = preflight(work, *validation_args())
    assert rc == 2, data
    assert_blocked(data, "case_colliding_paths")
    assert data["diff_metrics"]["case_collisions"], data


def _make_overlap_state(temp_dir: Path, origin: Path, work: Path) -> None:
    """Rebase onto advanced main so local changed paths overlap recent target changes."""
    (work / "shared.txt").write_text("base\n", encoding="utf-8")
    run(work, "git", "add", "shared.txt")
    run(work, "git", "commit", "-m", "add shared")
    run(work, "git", "push", "origin", "HEAD:main")
    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "checkout", "-B", "task/test", "origin/main")
    add_commit(work, "shared.txt", "feature side\n", "feature shared")

    other = clone_writer(temp_dir, origin, "main_writer")
    run(other, "git", "checkout", "-B", "main", "origin/main")
    add_commit(other, "shared.txt", "main mechanism\n", "main shared")
    run(other, "git", "push", "origin", "main")

    run(work, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    run(work, "git", "rebase", "refs/remotes/origin/main", check=False)
    (work / "shared.txt").write_text("feature side\n", encoding="utf-8")
    run(work, "git", "add", "shared.txt")
    run(work, "git", "rebase", "--continue", env={**__import__("os").environ, "GIT_EDITOR": "true"})


def acknowledge_overlap_passes(temp_dir: Path, origin: Path, work: Path) -> None:
    _make_overlap_state(temp_dir, origin, work)
    rc, data = preflight(work, "--acknowledge-recent-target-overlap", *validation_args())
    assert rc == 0, data
    assert data["can_push_main"] is True, data
    assert data["safety_scope"]["overlap_acknowledged"] is True, data
    assert "recent_target_overlap" not in data["blockers"], data
    assert data["main_is_ancestor"] is True, data
    assert "shared.txt" in data["diff_metrics"]["recent_target_overlap_paths"], data


def acknowledge_does_not_mask_other_blockers(temp_dir: Path, origin: Path, work: Path) -> None:
    _make_overlap_state(temp_dir, origin, work)
    (work / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    rc, data = preflight(work, "--acknowledge-recent-target-overlap", *validation_args())
    assert rc == 2, data
    assert data["can_push_main"] is False, data
    assert data["safety_scope"]["overlap_acknowledged"] is False, data
    assert "dirty_worktree" in data["blockers"], data
    assert "recent_target_overlap" in data["blockers"], data


CASES: list[tuple[str, Callable[[Path, Path, Path], None]]] = [
    ("clean_missing_remote_branch", clean_missing_remote_branch),
    ("validation_evidence_missing", validation_evidence_missing),
    ("remote_exists_tracking_ref_deleted", remote_exists_tracking_ref_deleted),
    ("dirty_worktree", dirty_worktree),
    ("hidden_local_change_blocks", hidden_local_change_blocks),
    ("target_not_ancestor", target_not_ancestor),
    ("diff_check_failed", diff_check_failed),
    ("skip_diff_check_blocks", skip_diff_check_blocks),
    ("no_fetch_blocks", no_fetch_blocks),
    ("validation_command_not_allowlisted", validation_command_not_allowlisted),
    ("diverged_feature_branch", diverged_feature_branch),
    ("remote_feature_branch_ahead_only", remote_feature_branch_ahead_only),
    ("no_commits_to_main", no_commits_to_main),
    ("ambiguous_origin_main_ref_blocks", ambiguous_origin_main_ref_blocks),
    ("source_must_be_head", source_must_be_head),
    ("detached_head_blocks", detached_head_blocks),
    ("replace_refs_block", replace_refs_block),
    ("feature_branch_remote_ref_shadow", feature_branch_remote_ref_shadow),
    ("validation_manifest_in_git_dir", validation_manifest_in_git_dir),
    ("merge_commit_blocks", merge_commit_blocks),
    ("rebase_in_progress_blocks", rebase_in_progress_blocks),
    ("same_file_conflict_drops_main_side", same_file_conflict_drops_main_side),
    ("delete_remote_changed_file_blocks", delete_remote_changed_file_blocks),
    ("rename_blocks", rename_blocks),
    ("case_collision_blocks", case_collision_blocks),
    ("acknowledge_overlap_passes", acknowledge_overlap_passes),
    ("acknowledge_does_not_mask_other_blockers", acknowledge_does_not_mask_other_blockers),
]


def main() -> int:
    passed: list[str] = []
    for name, test_case in CASES:
        temp_dir: Path | None = None
        try:
            temp_dir, origin, work = make_repo()
            test_case(temp_dir, origin, work)
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
        passed.append(name)

    print("passed:")
    for name in passed:
        print(f"- {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
