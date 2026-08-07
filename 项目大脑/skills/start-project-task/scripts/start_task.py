from __future__ import annotations

"""Start a concrete 项目大脑 task and update root workstate.json.

Usage:
    python 项目大脑/skills/start-project-task/scripts/start_task.py --author {author} --task-slug example --task-name "Example" --request "..." --goal "..."
"""

import argparse
import json
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


MAIN_BRANCHES = {"main", "master"}
FORK_SKIP_FILE_NAMES = {"任务范围.md", "任务指标.md"}


def run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(f"Cannot find repo root from {start}")


def load_workstate(repo_root: Path) -> dict[str, Any] | None:
    path = repo_root / "workstate.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Invalid workstate.json: expected object")
    return data


def load_workstate_author(repo_root: Path) -> str | None:
    workstate = load_workstate(repo_root)
    if not workstate:
        return None
    author = workstate.get("author")
    if author is not None and not isinstance(author, str):
        raise SystemExit("Invalid workstate.json author: expected string")
    return author


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "task"


def normalize_relative_path(repo_root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path is outside repo: {path}") from exc
    return relative.as_posix().rstrip("/") + "/"


def task_root(repo_root: Path, author: str) -> Path:
    return repo_root / "项目大脑" / "用户" / author / "tasks"


def is_under_task_root(repo_root: Path, author: str, path_text: str) -> bool:
    expected = normalize_relative_path(repo_root, task_root(repo_root, author))
    return (path_text.rstrip("/") + "/").startswith(expected)


def build_unique_task_folder(root: Path, task_slug: str, start_day: date) -> Path:
    base = f"{start_day.isoformat()}-{slugify(task_slug)}"
    candidate = root / base
    counter = 2
    while candidate.exists():
        candidate = root / f"{base}-{counter}"
        counter += 1
    return candidate


def resolve_task_folder(repo_root: Path, task_folder: str) -> Path:
    raw = Path(task_folder)
    path = raw if raw.is_absolute() else repo_root / raw
    try:
        path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Task folder is outside repo: {path}") from exc
    return path


def validate_fork_source(repo_root: Path, author: str, task_folder: str) -> Path:
    source = resolve_task_folder(repo_root, task_folder)
    if not source.exists() or not source.is_dir():
        raise SystemExit(f"Fork source task folder does not exist: {task_folder}")
    relative = normalize_relative_path(repo_root, source)
    if not is_under_task_root(repo_root, author, relative):
        raise SystemExit(
            "Fork source task folder must be under "
            f"{normalize_relative_path(repo_root, task_root(repo_root, author))}; got {relative!r}."
        )
    for required in ("process.md", "design.md"):
        if not (source / required).is_file():
            raise SystemExit(f"Fork source task folder must contain {required}: {relative}")
    return source


def copy_fork_source_task(source: Path, destination: Path) -> int:
    copied = 0
    for path in source.rglob("*"):
        relative_parts = path.relative_to(source).parts
        if any(part in {"_local", "raw-local", "fork-context"} for part in relative_parts):
            continue
        if path.name in FORK_SKIP_FILE_NAMES:
            continue
        target = destination / Path(*relative_parts)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def write_process_md(
    path: Path,
    author: str,
    task_name: str,
    request: str,
    goal: str,
    fork_source_relative: str | None = None,
) -> None:
    inherited_process = path.read_text(encoding="utf-8-sig").strip() if path.exists() else ""
    fork_text = ""
    if fork_source_relative:
        fork_text = (
            "\n## 继承上下文\n\n"
            f"- 本任务 fork 自 `{fork_source_relative}`。\n"
            "- 源 task 目录内容已复制到本任务目录，启动流程仍按普通任务读取 `process.md` 和 `design.md`。\n"
            "- 本文件顶部是当前任务目标；下方保留源任务 `process.md` 快照，仅作为历史上下文。\n"
            "- 后续记录只写当前任务，不回写旧任务。\n"
        )
        if inherited_process:
            fork_text += "\n---\n\n## Fork 源任务 process.md 快照\n\n" + inherited_process + "\n"
    text = (
        f"# {task_name}\n\n"
        f"开始日期: {date.today().isoformat()}\n\n"
        "## 用户原始需求\n\n"
        f"{request.strip()}\n\n"
        "## 目标\n\n"
        f"{goal.strip()}\n\n"
        "## 当前状态\n\n"
        "- 任务目录已创建。\n"
        "- 后续工作从本文件继续追加过程和验收摘要。\n"
        "- 稳定设计决策写入 `design.md`。\n\n"
        "## 团队经验引用\n\n"
        "- [ ] 已读 `项目大脑/团队经验/INDEX.md`，按本任务 request/goal 点读 1–3 篇 `entries/`\n"
        "- 相关条文 ID：（待填）\n"
        "- 与本任务关联：（待填）\n\n"
        "## 个人经验引用\n\n"
        f"- [ ] 已读 `项目大脑/用户/{author}/经验/INDEX.md`，按本任务 request/goal 点读 0–3 篇 `entries/`\n"
        "- 相关条文 ID：（待填；尚无 entries 时在关联说明写「暂无」）\n"
        "- 与本任务关联：（待填）\n\n"
        "## 团队公理引用\n\n"
        "- [ ] 已读 `项目大脑/公理/index.md`「任务开始路由」，按 request/goal 点读 0–2 篇 `entries/`\n"
        "- 推荐公理 ID：（待填；无匹配写「暂无」）\n"
        "- 与本任务关联：（待填）\n"
        f"{fork_text}"
    )
    path.write_text(text, encoding="utf-8")


def write_design_md(
    path: Path,
    task_name: str,
    fork_source_relative: str | None = None,
) -> None:
    inherited_design = path.read_text(encoding="utf-8-sig").strip() if path.exists() else ""
    fork_text = ""
    if fork_source_relative:
        fork_text = (
            "## 派生来源\n\n"
            f"- 本任务 fork 自 `{fork_source_relative}`。\n"
            "- 源 task 的设计记录已复制到本文件下方快照区；新决策写入“本任务新增决策”。\n\n"
            "## 本任务新增决策\n\n"
            "- 暂无。\n"
        )
        if inherited_design:
            fork_text += "\n---\n\n## Fork 源任务 design.md 快照\n\n" + inherited_design + "\n"
    else:
        fork_text = "## 决策记录\n\n- 暂无。\n"
    text = (
        f"# {task_name} 设计决策\n\n"
        f"开始日期: {date.today().isoformat()}\n\n"
        "本文件只记录本任务形成的稳定设计决策。过程、尝试、失败路径和验收流水写入 `process.md`。\n\n"
        f"{fork_text}"
    )
    path.write_text(text, encoding="utf-8")


def write_task_scope_md(path: Path, author: str, task_name: str, request: str, goal: str) -> None:
    def cell(value: str) -> str:
        return " ".join(value.strip().replace("|", "\\|").splitlines())

    path.write_text(
        "# 任务范围\n\n"
        "本文件记录当前任务的原子工作项、WIP、验证方法和证据。固定表格既方便人审阅，也方便 `project-info-healthcheck` 做最低限度检查。\n\n"
        "## 状态枚举\n\n"
        "- `not_started`\n"
        "- `in_progress`\n"
        "- `blocked`\n"
        "- `passing`\n\n"
        "## 规则\n\n"
        "- 同一时间最多一个 `in_progress`。\n"
        "- 新增或拆分工作项时追加行，不使用自增编号。\n"
        "- 每个工作项必须写清验证方法；完成后把状态改为 `passing` 并补证据。\n"
        "- 过程写 `process.md`，稳定决策写 `design.md`，长期指标写 `任务指标.md`。\n"
        "- 四件套就绪后、推进 `task-objective` 前：依次完成 `team-exp-routing`、`personal-exp-routing`、`team-axiom-routing`（见 `主流程.md` §3）。\n\n"
        "## 工作项\n\n"
        "| id | 标题 | 状态 | 行为 | 验证方法 | 证据 | 备注 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| team-exp-routing | 团队经验路由 | in_progress | 读 `团队经验/INDEX.md` → 按 request/goal 点读 1–3 篇 entries | `process.md`「团队经验引用」已填条文 ID 与关联说明 |  | 主流程 §3 |\n"
        f"| personal-exp-routing | 个人经验路由 | not_started | 读 `用户/{author}/经验/INDEX.md` → 按 request/goal 点读 0–3 篇 entries | `process.md`「个人经验引用」已填条文 ID 与关联说明（无条目写「暂无」） |  | 主流程 §3 |\n"
        "| team-axiom-routing | 团队公理路由 | not_started | 读 `公理/index.md`「任务开始路由」→ 点读 0–2 篇 entries | `process.md`「团队公理引用」已填 ID 与关联说明（无匹配写「暂无」） |  | 主流程 §3 |\n"
        f"| task-objective | {cell(task_name)} | not_started | {cell(goal)} | "
        "读取 `process.md` / `design.md`；按 skill `项目大脑/skills/project-validation/` 选择验收层级；把证据写回本表和 `process.md` |  | "
        f"{cell(request)} |\n",
        encoding="utf-8",
    )


def write_task_metrics_md(path: Path) -> None:
    path.write_text(
        "# 任务指标\n\n"
        "本文件记录当前任务的轻量度量，用于长期比较项目大脑是否降低返工、验证缺口和上下文重建成本。\n\n"
        "## 写入规则\n\n"
        "- 每轮阶段验收或收尾时追加一行。\n"
        "- 只写可观察事实，不写长篇过程；过程细节写 `process.md`。\n"
        "- 失败归因使用固定枚举：`任务规范`、`上下文`、`环境`、`验证`、`状态`、`范围`、`外部依赖`。\n\n"
        "## 指标表\n\n"
        "| 日期 | 任务阶段 | 声称完成 | 实际验收 | 失败归因 | 重建上下文耗时 | 范围漂移 | 证据 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n",
        encoding="utf-8",
    )


def markdown_cell(value: str) -> str:
    return " ".join(value.strip().replace("|", "\\|").splitlines())


def ensure_tasklist(path: Path) -> None:
    if path.exists():
        return
    path.write_text(
        "# 任务索引\n\n"
        "本文件只索引当前 author 做过的任务。不同用户的 `tasklist.md` 放在各自私人目录里，不互相合并。启动新任务前先查这里，判断是否能继续旧任务，避免重复开任务。\n\n"
        "## 合并规则\n\n"
        "- 本规则只处理同一个 author 的多个 worktree 同时修改本文件的情况；不同 author 的 `tasklist.md` 不合并。\n"
        "- 不使用自增编号，避免同一 author 的多个 worktree 同时新增任务时编号冲突。\n"
        "- 任务目录路径是唯一主键；合并冲突时保留双方新增的不同任务行。\n"
        "- 只保留一个表头；任务行按任务目录里的日期排序，同一天按任务目录名排序。\n"
        "- 如果同一个任务目录出现重复行，保留状态和最近更新较新的那一行。\n\n"
        "| 任务目录 | 任务名 | 摘要 | 状态 | 最近更新 |\n"
        "| --- | --- | --- | --- | --- |\n",
        encoding="utf-8",
    )


def append_tasklist_entry(
    repo_root: Path,
    author: str,
    task_folder: Path,
    task_name: str,
    summary: str,
) -> None:
    tasklist = repo_root / "项目大脑" / "用户" / author / "tasklist.md"
    ensure_tasklist(tasklist)
    relative = normalize_relative_path(repo_root, task_folder)
    entry = (
        f"| `{markdown_cell(relative)}` | {markdown_cell(task_name)} | "
        f"{markdown_cell(summary)} | 进行中 | {date.today().isoformat()} |\n"
    )
    tasklist.write_text(tasklist.read_text(encoding="utf-8") + entry, encoding="utf-8")


def write_workstate(repo_root: Path, author: str, task_folder: Path, source: str) -> None:
    data = load_workstate(repo_root) or {}
    relative = normalize_relative_path(repo_root, task_folder)
    data.update(
        {
            "author": author,
            "task_folder": relative,
            "source": source,
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "note": "Local cold-start state. Do not commit.",
        }
    )
    (repo_root / "workstate.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def print_main_workspace_reminder() -> None:
    print("当前在主工作区：本次改动会直接进入 main 的工作目录。")
    print("先按 worktree-gate 判断：有冲突、多人并行或需要独立试验时，才创建 worktree。")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start a project-info task.")
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument("--author", help="Current remote account / author folder name.")
    parser.add_argument("--task-name", required=True, help="Human-readable task name.")
    parser.add_argument("--task-slug", required=True, help="ASCII slug used in folder name.")
    parser.add_argument("--request", required=True, help="Original user request.")
    parser.add_argument("--goal", required=True, help="Concrete task goal.")
    parser.add_argument("--fork-from-task-folder", help="Existing task folder to copy into new task folder.")
    parser.add_argument(
        "--replace-current",
        action="store_true",
        help="Allow replacing an existing concrete task_folder in workstate.json.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    author = args.author or load_workstate_author(repo_root)
    if not author:
        raise SystemExit("Author is required. Pass --author or create root workstate.json.")

    branch = run_git(repo_root, "branch", "--show-current")
    if branch in MAIN_BRANCHES:
        print_main_workspace_reminder()

    root = task_root(repo_root, author)
    root.mkdir(parents=True, exist_ok=True)

    workstate = load_workstate(repo_root)
    if workstate:
        current_author = workstate.get("author")
        current_folder = workstate.get("task_folder")
        if current_author and current_author != author:
            raise SystemExit(
                f"workstate.json author is {current_author!r}, but requested {author!r}."
            )
        if not isinstance(current_folder, str):
            raise SystemExit("workstate.json task_folder must be a string.")
        if current_folder.strip():
            if not is_under_task_root(repo_root, author, current_folder):
                raise SystemExit(
                    "workstate.json task_folder must be under "
                    f"{normalize_relative_path(repo_root, root)}; got {current_folder!r}."
                )
            if not args.replace_current:
                raise SystemExit(
                    "workstate.json already points to a concrete task folder. "
                    "Ask the user whether to start a new task, then rerun with --replace-current."
                )

    fork_source = (
        validate_fork_source(repo_root, author, args.fork_from_task_folder)
        if args.fork_from_task_folder
        else None
    )
    fork_source_relative = normalize_relative_path(repo_root, fork_source) if fork_source else None

    folder = build_unique_task_folder(root, args.task_slug, date.today())
    folder.mkdir(parents=False)
    copied_fork_files = copy_fork_source_task(fork_source, folder) if fork_source else 0
    write_process_md(
        folder / "process.md",
        author,
        args.task_name,
        args.request,
        args.goal,
        fork_source_relative,
    )
    write_design_md(folder / "design.md", args.task_name, fork_source_relative)
    write_task_scope_md(folder / "任务范围.md", author, args.task_name, args.request, args.goal)
    write_task_metrics_md(folder / "任务指标.md")
    write_workstate(repo_root, author, folder, "task-forked" if fork_source else "task-started")
    append_tasklist_entry(repo_root, author, folder, args.task_name, args.goal)

    relative_folder = normalize_relative_path(repo_root, folder)
    print(f"新建当前任务目录: {relative_folder}")
    print(f"任务描述: 任务名={args.task_name}；摘要={args.goal}")
    print("请核对当前任务目录是否对应本轮目标；如果不正确，请停止并修正任务指针。")
    print(f"Created task folder: {relative_folder}")
    print("Wrote process.md")
    print("Wrote design.md")
    print("Wrote 任务范围.md")
    print("Wrote 任务指标.md")
    if fork_source:
        print(f"Copied fork source task files: {copied_fork_files}")
    print("Updated user tasklist.md")
    print("Updated workstate.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
