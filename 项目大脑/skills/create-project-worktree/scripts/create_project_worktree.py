from __future__ import annotations

"""Create a task worktree and bind project-info author state.

Usage:
    python 项目大脑/skills/create-project-worktree/scripts/create_project_worktree.py --author {author} --task-slug example --task-name "Example" --request "..." --goal "..."
"""

import argparse
import json
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse


AUTHOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FORK_SKIP_FILE_NAMES = {"任务范围.md", "任务指标.md"}


def run_git(cwd: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_text,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def run_git_optional(cwd: Path, *args: str, input_text: str | None = None) -> str:
    try:
        return run_git(cwd, *args, input_text=input_text)
    except subprocess.CalledProcessError:
        return ""


def run_codegraph_init(worktree_path: Path) -> str:
    script = Path(__file__).resolve().with_name("init_codegraph_for_worktree.bat")
    if not script.is_file():
        raise SystemExit(f"Missing CodeGraph init script: {script}")

    cmd_exe = shutil.which("cmd.exe") or "cmd.exe"
    result = subprocess.run(
        [cmd_exe, "/d", "/c", str(script), str(worktree_path)],
        cwd=worktree_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode != 0:
        detail = f"\n{output}" if output else ""
        raise SystemExit(f"CodeGraph init failed for worktree: {worktree_path}{detail}")
    return output


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(f"Cannot find repo root from {start}")


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "task"


def validate_author(author: str) -> str:
    if not AUTHOR_PATTERN.fullmatch(author):
        raise SystemExit(
            "Invalid author. Use 1-64 characters: letters, digits, dot, underscore, hyphen; "
            "the first character must be a letter or digit."
        )
    return author


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


def remote_url(repo_root: Path, remote: str) -> str:
    url = run_git(repo_root, "remote", "get-url", remote)
    if not url:
        raise SystemExit(f"Cannot read remote URL: {remote}")
    return url


def credential_query_from_url(url: str) -> tuple[str, str, str] | None:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        path = parsed.path.lstrip("/")
        return parsed.scheme, parsed.hostname, path

    match = re.match(r"(?:(?P<user>[^@]+)@)?(?P<host>[^:]+):(?P<path>.+)", url)
    if match:
        return "ssh", match.group("host"), match.group("path")
    return None


def username_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.username:
        return unquote(parsed.username)
    match = re.match(r"(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)", url)
    if match and match.group("user") not in {"git", "ssh"}:
        return match.group("user")
    return None


def username_from_credential(repo_root: Path, url: str) -> str | None:
    query = credential_query_from_url(url)
    if not query:
        return None
    protocol, host, path = query
    input_text = f"protocol={protocol}\nhost={host}\npath={path}\n\n"
    output = run_git_optional(repo_root, "credential", "fill", input_text=input_text)
    for line in output.splitlines():
        if line.startswith("username="):
            value = line.partition("=")[2].strip()
            return value or None
    return None


def resolve_author(repo_root: Path, remote: str, explicit_author: str | None) -> str:
    if explicit_author:
        return validate_author(explicit_author)

    workstate_author = load_workstate_author(repo_root)
    if workstate_author:
        return validate_author(workstate_author)

    url = remote_url(repo_root, remote)
    credential_user = username_from_credential(repo_root, url)
    if credential_user:
        return validate_author(credential_user)

    url_user = username_from_url(url)
    if url_user:
        return validate_author(url_user)

    raise SystemExit("Cannot determine remote account author. Pass --author explicitly.")


def identity_email(author: str, explicit_email: str | None, email_domain: str) -> str:
    if explicit_email:
        return explicit_email
    return f"{author}@{email_domain}"


def unique_worktree_path(repo_root: Path, task_slug: str, start_day: date, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path).resolve()

    base_name = f"{repo_root.name}-{start_day.isoformat()}-{slugify(task_slug)}"
    candidate = repo_root.parent / base_name
    counter = 2
    while candidate.exists():
        candidate = repo_root.parent / f"{base_name}-{counter}"
        counter += 1
    return candidate.resolve()


def ref_exists(repo_root: Path, ref: str) -> bool:
    return bool(run_git_optional(repo_root, "show-ref", "--verify", ref))


def unique_branch_name(repo_root: Path, kind: str, author: str, task_slug: str, start_day: date) -> str:
    base = f"{kind}/{author}-{start_day.isoformat()}-{slugify(task_slug)}"
    candidate = base
    counter = 2
    while ref_exists(repo_root, f"refs/heads/{candidate}") or ref_exists(repo_root, f"refs/remotes/origin/{candidate}"):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def normalize_relative_path(repo_root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path is outside repo: {path}") from exc
    return relative.as_posix().rstrip("/") + "/"


def task_root(repo_root: Path, author: str) -> Path:
    return repo_root / "项目大脑" / "用户" / author / "tasks"


def build_unique_task_folder(root: Path, task_slug: str, start_day: date) -> Path:
    base = f"{start_day.isoformat()}-{slugify(task_slug)}"
    candidate = root / base
    counter = 2
    while candidate.exists():
        candidate = root / f"{base}-{counter}"
        counter += 1
    return candidate


def write_workstate(repo_root: Path, author: str, email: str, remote: str, task_folder: Path, source: str) -> None:
    data = {
        "author": author,
        "task_folder": normalize_relative_path(repo_root, task_folder),
        "git_user_name": author,
        "git_user_email": email,
        "remote": remote,
        "source": source,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "Local cold-start state. Do not commit.",
    }
    (repo_root / "workstate.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
    expected = normalize_relative_path(repo_root, task_root(repo_root, author))
    if not relative.startswith(expected):
        raise SystemExit(f"Fork source task folder must be under {expected}; got {relative!r}.")
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
        "- 任务 worktree 已创建。\n"
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


def create_task_files(
    repo_root: Path,
    author: str,
    task_slug: str,
    task_name: str,
    request: str,
    goal: str,
    fork_source: Path | None = None,
    fork_source_relative: str | None = None,
) -> tuple[Path, int]:
    root = task_root(repo_root, author)
    root.mkdir(parents=True, exist_ok=True)
    folder = build_unique_task_folder(root, task_slug, date.today())
    folder.mkdir(parents=False)
    copied_fork_files = copy_fork_source_task(fork_source, folder) if fork_source else 0
    write_process_md(folder / "process.md", author, task_name, request, goal, fork_source_relative)
    write_design_md(folder / "design.md", task_name, fork_source_relative)
    write_task_scope_md(folder / "任务范围.md", author, task_name, request, goal)
    write_task_metrics_md(folder / "任务指标.md")
    append_tasklist_entry(repo_root, author, folder, task_name, goal)
    return folder, copied_fork_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a project-info task worktree.")
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument("--remote", default="origin", help="Git remote name.")
    parser.add_argument("--base", default="origin/main", help="Base ref for the new worktree.")
    parser.add_argument("--author", help="Remote account author. Required if it cannot be detected.")
    parser.add_argument("--email", help="Git author email. Defaults to {author}@{email-domain}.")
    parser.add_argument("--email-domain", default="kingsoft.com", help="Default email domain.")
    parser.add_argument("--kind", default="task", help="Branch kind prefix.")
    parser.add_argument("--worktree-path", help="Explicit new worktree path.")
    parser.add_argument("--task-name", required=True, help="Human-readable task name.")
    parser.add_argument("--task-slug", required=True, help="ASCII slug used in branch and folder names.")
    parser.add_argument("--request", required=True, help="Original user request.")
    parser.add_argument("--goal", required=True, help="Concrete task goal.")
    parser.add_argument("--fork-from-task-folder", help="Existing task folder to copy into new task folder.")
    parser.add_argument("--no-fetch", action="store_true", help="Skip fetch before creating the worktree.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without writing files.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    author = resolve_author(repo_root, args.remote, args.author)
    email = identity_email(author, args.email, args.email_domain)
    start_day = date.today()
    branch = unique_branch_name(repo_root, args.kind, author, args.task_slug, start_day)
    worktree_path = unique_worktree_path(repo_root, args.task_slug, start_day, args.worktree_path)
    fork_source = (
        validate_fork_source(repo_root, author, args.fork_from_task_folder)
        if args.fork_from_task_folder
        else None
    )
    fork_source_relative = normalize_relative_path(repo_root, fork_source) if fork_source else None

    if args.dry_run:
        print(f"Author: {author}")
        print(f"Email: {email}")
        print(f"Base: {args.base}")
        print(f"Branch: {branch}")
        print(f"Worktree: {worktree_path}")
        if fork_source_relative:
            print(f"Fork from task folder: {fork_source_relative}")
        print("Would create task folder and write workstate.json")
        return 0

    if worktree_path.exists():
        raise SystemExit(f"Worktree path already exists: {worktree_path}")

    if not args.no_fetch and args.base.startswith(f"{args.remote}/"):
        remote_branch = args.base.split("/", 1)[1]
        run_git(repo_root, "fetch", args.remote, remote_branch)

    run_git(repo_root, "worktree", "add", "-b", branch, str(worktree_path), args.base)

    run_git(worktree_path, "config", "user.name", author)
    run_git(worktree_path, "config", "user.email", email)
    task_folder, copied_fork_files = create_task_files(
        worktree_path,
        author,
        args.task_slug,
        args.task_name,
        args.request,
        args.goal,
        fork_source,
        fork_source_relative,
    )
    write_workstate(
        worktree_path,
        author,
        email,
        args.remote,
        task_folder,
        "worktree-created-from-task-fork" if fork_source else "worktree-created",
    )
    codegraph_output = run_codegraph_init(worktree_path)

    relative_task_folder = normalize_relative_path(worktree_path, task_folder)
    print(f"Created worktree: {worktree_path}")
    print(f"Created branch: {branch}")
    print(f"Anchored Git identity: {author} <{email}>")
    print(f"Wrote workstate.json author: {author}")
    print(f"新建当前任务目录: {relative_task_folder}")
    print(f"任务描述: 任务名={args.task_name}；摘要={args.goal}")
    print("请核对当前任务目录是否对应本轮目标；如果不正确，请停止并修正任务指针。")
    print(f"Created task folder: {relative_task_folder}")
    print("Wrote process.md")
    print("Wrote design.md")
    print("Wrote 任务范围.md")
    print("Wrote 任务指标.md")
    if fork_source:
        print(f"Copied fork source task files: {copied_fork_files}")
    print("Initialized CodeGraph with init_codegraph_for_worktree.bat")
    if codegraph_output:
        print(codegraph_output)
    print("Updated user tasklist.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
