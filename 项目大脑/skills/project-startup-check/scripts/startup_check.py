from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
INIT_USER_SCRIPT_DIR = SCRIPT_DIR.parent.parent / "init-project-info-user" / "scripts"
if str(INIT_USER_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(INIT_USER_SCRIPT_DIR))

from author_aliases import resolve_canonical_author


INFO_DIR = "项目大脑"
USER_DIR = "用户"
MAIN_BRANCHES = {"main", "master"}
USER_BASE_FILES = [
    "私有流程.md",
    "私有规则.md",
    "index.md",
    "职责范围.md",
    "用户画像.md",
    "工作习惯.md",
    "协作灵魂.md",
    "沟通规范.md",
    "沟通偏好.md",
    "tasklist.md",
]
CURRENT_TASK_FILES = ["process.md", "design.md", "任务范围.md", "任务指标.md"]
USER_BASE_DIRS = ["经验", "代码地图", "资料地图", "tasks"]


@dataclass
class StartupResult:
    repo_root: str
    branch: str = ""
    author: str = ""
    user_description: str = ""
    task_folder: str = ""
    task_description: str = ""
    next_action: str = ""
    repaired: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def exit_code(self) -> int:
        if self.errors:
            if any(error.startswith("main_branch_blocked:") for error in self.errors):
                return 2
            return 1
        return 0


class StartupCheck:
    def __init__(
        self,
        repo_root: Path,
        author_arg: str,
        intends_file_change: bool,
        check_only: bool,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.author_arg = author_arg.strip()
        self.intends_file_change = intends_file_change
        self.check_only = check_only
        self.info_root = self.repo_root / INFO_DIR
        self.result = StartupResult(repo_root=str(self.repo_root))

    def run(self) -> StartupResult:
        self.check_entry_files()
        self.check_branch()
        if self.result.errors:
            self.load_existing_workstate_for_summary()
            self.enrich_context_summary()
            return self.result
        self.ensure_workstate()
        if self.result.errors:
            self.enrich_context_summary()
            return self.result
        self.ensure_user_and_extension_dirs()
        self.check_current_task()
        self.enrich_context_summary()
        return self.result

    def check_entry_files(self) -> None:
        for relative in [
            "AGENTS.md",
            f"{INFO_DIR}/index.md",
            f"{INFO_DIR}/启动.md",
            f"{INFO_DIR}/主流程.md",
            f"{INFO_DIR}/主规则.md",
        ]:
            if not (self.repo_root / relative).exists():
                self.result.errors.append(f"missing_required_entry:{relative}")

    def check_branch(self) -> None:
        branch = self.run_git("branch", "--show-current")
        self.result.branch = branch

    def ensure_workstate(self) -> None:
        path = self.repo_root / "workstate.json"
        if not path.exists():
            if not self.author_arg:
                self.result.errors.append(
                    "workstate_missing:pass --author after user confirms the remote account name"
                )
                self.result.next_action = "confirm_author"
                return
            if self.check_only:
                self.result.warnings.append("workstate_missing")
                self.result.author = self.normalize_author(self.author_arg)
                self.result.next_action = "generate_workstate"
                return
            self.run_python_script(
                "generate-workstate/scripts/generate_workstate.py",
                "--author",
                self.author_arg,
                "--source",
                "startup-check",
            )
            self.result.repaired.append("workstate.json")

        data = self.load_workstate(path)
        if data is None:
            return
        author = data.get("author")
        task_folder = data.get("task_folder")
        if not isinstance(author, str) or not author.strip():
            if not self.author_arg:
                self.result.errors.append("workstate_author_invalid:author is missing or not a string")
                self.result.next_action = "confirm_author"
                return
            if self.check_only:
                self.result.warnings.append("workstate_author_invalid")
                author = self.author_arg
            else:
                self.run_python_script(
                    "generate-workstate/scripts/generate_workstate.py",
                    "--author",
                    self.author_arg,
                    "--source",
                    "startup-check-author-repair",
                )
                self.result.repaired.append("workstate.json.author")
                data = self.load_workstate(path) or {}
                author = data.get("author")
                task_folder = data.get("task_folder")
        if not isinstance(task_folder, str):
            self.result.errors.append("workstate_task_folder_invalid:task_folder must be a string")
            return

        self.result.author = self.normalize_author(author.strip())
        self.result.task_folder = task_folder.strip()

    def load_existing_workstate_for_summary(self) -> None:
        path = self.repo_root / "workstate.json"
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        author = data.get("author")
        task_folder = data.get("task_folder")
        if isinstance(author, str):
            self.result.author = self.normalize_author(author.strip())
        if isinstance(task_folder, str):
            self.result.task_folder = task_folder.strip()

    def normalize_author(self, author: str) -> str:
        canonical = resolve_canonical_author(author)
        if canonical != author:
            self.result.warnings.append(f"author_alias_resolved:{author}->{canonical}")
        return canonical

    def ensure_user_and_extension_dirs(self) -> None:
        author = self.result.author
        if not author:
            return
        missing_user_paths = self.find_missing_user_paths(author)
        missing_extension_paths = self.find_missing_extension_paths(author)
        if self.check_only:
            for path in [*missing_user_paths, *missing_extension_paths]:
                self.result.warnings.append(f"missing_path:{self.rel(path)}")
            return
        if missing_user_paths:
            self.run_python_script("init-project-info-user/scripts/init_user.py", "--author", author)
            self.result.repaired.append(f"{INFO_DIR}/{USER_DIR}/{author}/")
        if missing_extension_paths:
            self.run_python_script(
                "project-info-extension-standards/scripts/ensure_extension_dirs.py",
                "--author",
                author,
            )
            self.result.repaired.append("extension_dirs")

        for path in [*self.find_missing_user_paths(author), *self.find_missing_extension_paths(author)]:
            self.result.errors.append(f"missing_after_repair:{self.rel(path)}")

    def check_current_task(self) -> None:
        author = self.result.author
        task_folder = self.result.task_folder
        if not author:
            return
        if not task_folder:
            self.result.warnings.append("task_folder_empty")
            self.result.next_action = "select_start_resume_or_fork_task"
            return
        task_path = (self.repo_root / task_folder).resolve()
        user_root = (self.info_root / USER_DIR / author).resolve()
        try:
            task_path.relative_to(user_root)
        except ValueError:
            self.result.errors.append(f"task_outside_user:{task_folder}")
            return
        if not task_path.is_dir():
            self.result.errors.append(f"task_missing:{task_folder}")
            return
        for filename in CURRENT_TASK_FILES:
            if not (task_path / filename).is_file():
                self.result.errors.append(f"task_file_missing:{task_folder.rstrip('/')}/{filename}")
        if not self.result.errors:
            self.result.next_action = "read_current_task_then_main_flow"

    def find_missing_user_paths(self, author: str) -> list[Path]:
        user_root = self.info_root / USER_DIR / author
        paths = [user_root / filename for filename in USER_BASE_FILES]
        paths.extend(user_root / dirname for dirname in USER_BASE_DIRS)
        paths.append(user_root / "代码地图" / "index.md")
        paths.append(user_root / "代码地图" / "差异列表.md")
        paths.append(user_root / "资料地图" / "index.md")
        return [path for path in [user_root, *paths] if not path.exists()]

    def find_missing_extension_paths(self, author: str) -> list[Path]:
        paths = [
            self.info_root / USER_DIR / author / "skills",
            self.info_root / USER_DIR / author / "mcps",
            self.info_root / USER_DIR / author / "hooks",
        ]
        return [path for path in paths if not path.exists()]

    def load_workstate(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            self.result.errors.append(f"workstate_invalid_json:{exc}")
            return None
        if not isinstance(data, dict):
            self.result.errors.append("workstate_invalid_type:expected JSON object")
            return None
        return data

    def enrich_context_summary(self) -> None:
        if self.result.author:
            self.result.user_description = self.describe_user(self.result.author)
        if self.result.author and self.result.task_folder:
            self.result.task_description = self.describe_task(
                self.result.author,
                self.result.task_folder,
            )

    def describe_user(self, author: str) -> str:
        user_root = self.info_root / USER_DIR / author
        profile = self.read_markdown_table(user_root / "用户画像.md")
        role_scope = self.read_blockquote_fields(user_root / "职责范围.md")

        member = profile.get("成员", "")
        role = profile.get("角色类型") or role_scope.get("角色类型", "")
        nicknames = profile.get("昵称 / 转写别名", "")
        responsibility = self.first_list_item_after_heading(user_root / "用户画像.md", "背景与职责摘要")
        if not responsibility:
            responsibility = self.first_list_item_after_heading(user_root / "职责范围.md", "主要职责")

        parts = [f"author={author}"]
        if member:
            parts.append(f"成员={member}")
        if role:
            parts.append(f"角色类型={role}")
        if nicknames:
            parts.append(f"昵称/转写别名={nicknames}")
        if responsibility:
            parts.append(f"职责摘要={responsibility}")
        return "；".join(parts)

    def describe_task(self, author: str, task_folder: str) -> str:
        task_from_list = self.describe_task_from_tasklist(author, task_folder)
        if task_from_list:
            return task_from_list
        process_path = self.repo_root / task_folder / "process.md"
        if process_path.is_file():
            heading = self.first_markdown_heading(process_path)
            if heading:
                return f"任务标题={heading}"
        return ""

    def describe_task_from_tasklist(self, author: str, task_folder: str) -> str:
        tasklist_path = self.info_root / USER_DIR / author / "tasklist.md"
        if not tasklist_path.is_file():
            return ""
        target = task_folder.rstrip("/") + "/"
        try:
            lines = tasklist_path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return ""
        for line in lines:
            if f"`{target}`" not in line:
                continue
            cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
            if len(cells) < 5:
                continue
            return (
                f"任务名={cells[1]}；摘要={cells[2]}；状态={cells[3]}；最近更新={cells[4]}"
            )
        return ""

    def read_markdown_table(self, path: Path) -> dict[str, str]:
        if not path.is_file():
            return {}
        result: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return {}
        for line in lines:
            if not line.startswith("|"):
                continue
            cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
            if len(cells) != 2 or cells[0] in {"项", "---"}:
                continue
            result[cells[0]] = cells[1]
        return result

    def read_blockquote_fields(self, path: Path) -> dict[str, str]:
        if not path.is_file():
            return {}
        result: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return {}
        for line in lines:
            if not line.startswith(">") or ":" not in line:
                continue
            key, value = line[1:].split(":", 1)
            result[key.strip()] = value.strip().strip("`")
        return result

    def first_list_item_after_heading(self, path: Path, heading: str) -> str:
        if not path.is_file():
            return ""
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return ""
        in_section = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                in_section = stripped.lstrip("#").strip() == heading
                continue
            if in_section and stripped.startswith("- "):
                return stripped[2:].strip()
        return ""

    def first_markdown_heading(self, path: Path) -> str:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    def run_python_script(self, relative_script: str, *args: str) -> None:
        script = self.info_root / "skills" / relative_script
        if not script.is_file():
            self.result.errors.append(f"script_missing:{self.rel(script)}")
            return
        command = [
            sys.executable,
            str(script),
            "--repo-root",
            str(self.repo_root),
            *args,
        ]
        completed = subprocess.run(
            command,
            cwd=self.repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            self.result.errors.append(
                f"script_failed:{self.rel(script)}:{completed.stderr.strip() or completed.stdout.strip()}"
            )

    def run_git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            self.result.errors.append(f"git_failed:{' '.join(args)}:{completed.stderr.strip()}")
            return ""
        return completed.stdout.strip()

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.repo_root).as_posix()
        except ValueError:
            return str(path)


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(f"Cannot find repo root from {start}")


def print_text(result: StartupResult) -> None:
    print("开始启动项目大脑")
    for item in result.repaired:
        print(f"REPAIRED {item}")
    for item in result.warnings:
        print(f"WARN {item}")
    for item in result.errors:
        print(f"ERROR {item}")
    print(f"startup-check: branch={result.branch or '<unknown>'} author={result.author or '<missing>'}")
    print(f"startup-check: task_folder={result.task_folder or '<empty>'}")
    print(f"startup-check: next_action={result.next_action or '<none>'}")
    print(f"startup-check: errors={len(result.errors)} warnings={len(result.warnings)}")
    workspace_kind = "主工作区（修改会直接进入 main）" if result.branch in MAIN_BRANCHES else "独立任务副本（修改尚未进入 main）"
    print(f"当前工作位置：{workspace_kind}")
    print(f"当前位置：{result.repo_root}")
    print(f"快速打开当前文件夹：python 项目大脑/skills/project-startup-check/scripts/startup_check.py --repo-root . --open-workspace")
    if result.branch in MAIN_BRANCHES:
        print("worktree 提醒：会议纪要、设计讨论和小改动可直接在这里工作；有冲突、多人并行或需要独立试验时才创建 worktree。")
    if result.author:
        print(f"识别到当前用户: {result.author}")
        if result.user_description:
            print(f"用户描述: {result.user_description}")
        print("请核对用户识别是否正确；如果不正确，请停止并修正 workstate.json.author。")
    else:
        print("未识别到当前用户；需要先确认 author，再继续启动。")

    if result.task_folder:
        print(f"找到当前任务目录: {result.task_folder}")
        if result.task_description:
            print(f"任务描述: {result.task_description}")
        print("请核对当前任务目录是否对应本轮目标；如果不正确，请切换、继续或新建任务。")
    else:
        print("当前没有绑定任务目录；需要先选择继续旧任务、fork 旧任务或新建任务。")

    print("启动阶段结束")
    if result.next_action == "read_current_task_then_main_flow":
        print("下一步需要加载公共流程和规则、当前用户私有流程和规则、当前任务上下文。")
    elif result.next_action == "select_start_resume_or_fork_task":
        print("启动阶段已停在任务选择；请先确认继续旧任务、fork 旧任务或新建任务。")
    elif result.next_action == "confirm_author":
        print("启动阶段已停在用户确认；请先确认当前操作者 author。")


def open_workspace(repo_root: Path) -> None:
    if os.name != "nt":
        raise SystemExit("--open-workspace 目前只支持 Windows 资源管理器。")
    os.startfile(str(repo_root))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run project-info startup checks.")
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument("--author", default="", help="Confirmed author used when workstate.json is missing.")
    parser.add_argument(
        "--intends-file-change",
        action="store_true",
        help="Show the workspace context when the current request intends to modify repository files.",
    )
    parser.add_argument("--check-only", action="store_true", help="Check only; do not create missing files.")
    parser.add_argument("--open-workspace", action="store_true", help="Open the current repository folder in Windows Explorer.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    check = StartupCheck(
        repo_root=repo_root,
        author_arg=args.author,
        intends_file_change=args.intends_file_change,
        check_only=args.check_only,
    )
    result = check.run()
    if args.json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    if args.open_workspace:
        open_workspace(Path(result.repo_root))
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
