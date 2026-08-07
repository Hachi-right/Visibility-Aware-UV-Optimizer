from __future__ import annotations

"""Healthcheck for 项目大脑.

This script checks the project-info entry chain, workstate, user area,
current task files, public indexes, skill references, and markdown coverage.
"""

import argparse
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


INFO_DIR = "项目大脑"
USER_DIR = "用户"
MAIN_FLOW = "主流程.md"
MAIN_RULES = "主规则.md"
STARTUP = "启动.md"
DESIGN_HTML = "说明.html"
ARTIFACT_TAXONOMY_REFERENCE = (
    "skills/project-info-artifact-taxonomy/references/markdown-artifacts.md"
)
CURATION_HARD_RULES_REFERENCE = (
    "skills/personal-experience-curation/references/curation-hard-rules.md"
)
DEPRECATED_PUBLIC_SKILLS = (
    "summarize-private-experience",
    "project-document-ingest",
    "report-communication",
    "private-experience-curation",
    "wps-cloud-doc-browser",
    "leader-public-curation",
    "project-info-dashboard",
    "web-data-report",
    "collaborative-file-import",
    "dialogue-transcript-ingest",
    "meeting-minutes-generation",
    "project-assistant-tools",
    "planner-submit-scope-review",
    "safe-branch-merge",
    "restruct-to-programmer-design",
    "decouple-framework-business",
)
INBOX_RULES_SKILL = "skills/project-inbox-rules"
CODEX_HOOKS_JSON = ".codex/hooks.json"
CURSOR_HOOKS_JSON = ".cursor/hooks.json"
CLAUDE_SETTINGS_JSON = ".claude/settings.json"
COMPACT_REMINDER_SCRIPT = "hooks/project_info_compact_reminder.py"
TURN_REMINDER_SCRIPT = "hooks/project_info_turn_reminder.py"

ROOT_ENTRY_FILES = ["AGENTS.md", "CLAUDE.md"]
ADAPTER_ENTRY_FILES = [".cursor/rules/project-rules.mdc", ".github/copilot-instructions.md"]
AGENTS_MAX_LINES = 40
INDEX_MAX_ENTRIES = 50
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
USER_BASE_DIRS = ["经验", "代码地图", "资料地图", "tasks", "skills", "mcps", "hooks"]
CURRENT_TASK_FILES = ["process.md", "design.md", "任务范围.md", "任务指标.md"]
KNOWN_TASK_MD = {
    "process.md",
    "design.md",
    "任务范围.md",
    "任务指标.md",
    "handoff.md",
    "交接说明.md",
    "clean-state-checklist.md",
    "evaluator-rubric.md",
    "quality-document.md",
}
TASK_ALIGNMENT_ARTIFACT = "给{recipient}的对齐说明.md"
TASK_DATED_NOTE_ARTIFACT = "YYYY-MM-DD-{topic}.md"
TASK_DATED_NOTE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
TASK_SCOPE_COLUMNS = ["id", "标题", "状态", "行为", "验证方法", "证据", "备注"]
TASK_SCOPE_STATUSES = {"not_started", "in_progress", "blocked", "passing"}
PRIVATE_USER_BOUNDARY_MARKER = "不得直接修改其他 author"
RESPONSIBILITY_SCOPE_REFUSAL_MARKER = "先明确拒绝"
RESPONSIBILITY_SCOPE_RISK_MARKER = "说明风险"
RESPONSIBILITY_SCOPE_SYNC_MARKER = "主动同步"
RESPONSIBILITY_SCOPE_DELIVERY_MARKER = "交付规则文档"
RESPONSIBILITY_PRIVATE_EXTENSION_MARKER = "辅助落盘默认先放当前 author 私有区"
PRIVATE_INDEX_FORBIDDEN_DEFAULT_SECTIONS = ["## 项目大脑入口", "## 项目公共文档入口"]
PRIVATE_EXTENSION_DEFAULT_MARKER = "默认放当前 author 私有区"
LEADER_PUBLICIZATION_MARKER = "leader 公共化"
SINGLE_ITEM_TOPIC_FILE_PATTERN = re.compile(r"^(?:\d{4}[-_年]|\d{8}|P\d+|[A-Za-z]{1,4}-?\d{1,}|条目\d+|经验\d+)$", re.I)
CODE_MAP_LINE_REF_PATTERN = re.compile(
    r"\.(?:py|ts|tsx|js|jsx|vue|cs|cpp|c|h|hpp|java|go|rs|md|json|yaml|yml|ini|proto|lua|mmd):\d+"
)


def posix_join(*parts: str) -> str:
    return posixpath.normpath(posixpath.join(*parts)).replace("\\", "/")

EXPECTED_ARTIFACT_NAMES = [
    "AGENTS.md",
    "agent工作规则.md",
    "CLAUDE.md",
    ".cursor/rules/project-rules.mdc",
    ".github/copilot-instructions.md",
    "主流程.md",
    "主规则.md",
    "项目大脑/index.md",
    "索引/",
    "代码地图/",
    "资料地图/",
    "初始分工表.md",
    "工具经验.md",
    "第三方辅助工具.md",
    "公理/",
    "私有流程.md",
    "私有规则.md",
    "协作灵魂.md",
    "沟通规范.md",
    "docs/meeting/*.md",
    "docs/会议转写术语表.md",
    "用户/{author}/index.md",
    "职责范围.md",
    "用户画像.md",
    "工作习惯.md",
    "沟通偏好.md",
    "经验/",
    "私人整理状态.md",
    "leader整理状态.md",
    "整理确认.md",
    "leader整理确认.md",
    "tasklist.md",
    "tasks/",
    "process.md",
    "design.md",
    "任务范围.md",
    "任务指标.md",
    "handoff.md",
    "交接说明.md",
    "clean-state-checklist.md",
    "evaluator-rubric.md",
    "quality-document.md",
    "ARCHITECTURE.md",
    "CONSTRAINTS.md",
]
EXPECTED_PUBLIC_INDEX_PATHS = [
    "../docs/agent工作规则.md",
    "../docs/会议转写术语表.md",
    "../docs/meeting/YYYY-MM-DD_AI开发下的程序与策划沟通方式.md",
]

@dataclass
class Issue:
    level: str
    code: str
    message: str


class Healthcheck:
    def __init__(self, repo_root: Path, allow_missing_workstate: bool = False) -> None:
        self.repo_root = repo_root
        self.info_root = repo_root / INFO_DIR
        self.allow_missing_workstate = allow_missing_workstate
        self.issues: list[Issue] = []

    def error(self, code: str, message: str) -> None:
        self.issues.append(Issue("ERROR", code, message))

    def warn(self, code: str, message: str) -> None:
        self.issues.append(Issue("WARN", code, message))

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8-sig")

    def check_exists(self, path: Path, code: str) -> bool:
        if not path.exists():
            self.error(code, f"Missing required path: {self.rel(path)}")
            return False
        return True

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return str(path)

    def run(self) -> int:
        self.check_entry_chain()
        self.check_agent_compact_hooks()
        self.check_public_roots()
        workstate = self.check_workstate()
        self.check_index_size()
        self.check_public_index()
        self.check_external_skill_references()
        self.check_skill_indexes()
        self.check_markdown_coverage()
        self.check_topic_cluster_documents()
        self.check_code_map_line_references()
        self.check_design_html()
        self.check_main_flow_does_not_reverse_reference_startup()
        self.check_main_flow_runtime_hub()
        self.check_private_extension_default_rules()
        self.check_user_private_boundary_rules()
        self.check_experience_curation_rules()
        if workstate:
            self.check_user_and_current_task(workstate)
        errors = [issue for issue in self.issues if issue.level == "ERROR"]
        warnings = [issue for issue in self.issues if issue.level == "WARN"]
        for issue in self.issues:
            print(f"{issue.level} {issue.code}: {issue.message}")
        print(f"project-info-healthcheck: errors={len(errors)} warnings={len(warnings)}")
        return 1 if errors else 0

    def check_entry_chain(self) -> None:
        for name in ROOT_ENTRY_FILES:
            self.check_exists(self.repo_root / name, f"entry.{name}.missing")
        for name in ADAPTER_ENTRY_FILES:
            self.check_exists(self.repo_root / name, f"entry.{name}.missing")
        if (self.repo_root / "RULES.md").exists():
            self.error("entry.RULES.md.exists", "RULES.md is deprecated; keep AGENTS.md as the single short entry")

        agents_path = self.repo_root / "AGENTS.md"
        if agents_path.exists():
            text = self.read_text(agents_path).replace("\\", "/")
            if f"{INFO_DIR}/{STARTUP}" not in text:
                self.error("entry.AGENTS.md.chain", f"AGENTS.md does not reference {INFO_DIR}/{STARTUP}")
            for needle in ["项目概述", "常用目录", "重要文档", "docs/规则/index.md", "docs/Specs/"]:
                if needle not in text:
                    self.error("entry.AGENTS.md.navigation", f"AGENTS.md missing lightweight navigation marker: {needle}")
            for needle in [f"{INFO_DIR}/skills/", f"{INFO_DIR}/用户/{{author}}/skills/"]:
                if needle not in text:
                    self.error("entry.AGENTS.md.skill_roots", f"AGENTS.md missing project-info skill root marker: {needle}")
            for forbidden in ["RULES.md", f"{INFO_DIR}/{MAIN_FLOW}", f"{INFO_DIR}/{MAIN_RULES}"]:
                if forbidden in text:
                    self.error("entry.AGENTS.md.too_explicit", f"AGENTS.md should not expand startup internals: {forbidden}")
            line_count = len([line for line in text.splitlines() if line.strip()])
            if line_count > AGENTS_MAX_LINES:
                self.error("entry.AGENTS.md.length", f"AGENTS.md has {line_count} non-empty lines; keep it <= {AGENTS_MAX_LINES}")
            if "```mermaid" in text.lower():
                self.error("entry.AGENTS.md.mermaid", "AGENTS.md must not contain Mermaid diagrams; move diagrams to docs")

        for name in [*ROOT_ENTRY_FILES[1:], *ADAPTER_ENTRY_FILES]:
            path = self.repo_root / name
            if not path.exists():
                continue
            text = self.read_text(path).replace("\\", "/")
            if "AGENTS.md" not in text:
                self.error(f"entry.{name}.chain", f"{name} does not reference AGENTS.md")
            if "RULES.md" in text:
                self.error(f"entry.{name}.rules", f"{name} still references deprecated RULES.md")

    def check_agent_compact_hooks(self) -> None:
        hooks_path = self.repo_root / CODEX_HOOKS_JSON
        cursor_hooks_path = self.repo_root / CURSOR_HOOKS_JSON
        claude_settings_path = self.repo_root / CLAUDE_SETTINGS_JSON
        script_path = self.repo_root / COMPACT_REMINDER_SCRIPT
        turn_script_path = self.repo_root / TURN_REMINDER_SCRIPT
        if not self.check_exists(hooks_path, "codex_hook.config.missing"):
            return
        self.check_exists(script_path, "compact_hook.shared_script.missing")
        self.check_exists(turn_script_path, "turn_hook.shared_script.missing")
        try:
            data = json.loads(self.read_text(hooks_path))
        except json.JSONDecodeError as exc:
            self.error("codex_hook.config.invalid_json", f"{CODEX_HOOKS_JSON} is not valid JSON: {exc}")
            return
        hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
        if not isinstance(hooks, dict):
            self.error("codex_hook.config.shape", f"{CODEX_HOOKS_JSON} hooks must be an object")
            return
        expected_codex_events = {
            "SessionStart": ["startup|resume|clear|compact", COMPACT_REMINDER_SCRIPT, "--agent codex --event session-start"],
            "PreCompact": ["manual|auto", COMPACT_REMINDER_SCRIPT, "--agent codex --event pre-compact"],
            "PostCompact": ["manual|auto", COMPACT_REMINDER_SCRIPT, "--agent codex --event post-compact"],
        }
        for event, markers in expected_codex_events.items():
            entries = hooks.get(event)
            if not isinstance(entries, list) or not entries:
                self.error("codex_hook.event.missing", f"{CODEX_HOOKS_JSON} must configure {event}")
                continue
            command_text = json.dumps(entries, ensure_ascii=False).replace("\\", "/")
            for marker in markers:
                if marker not in command_text:
                    self.error("codex_hook.event.command", f"{event} hook missing marker: {marker}")
        if not self.check_exists(cursor_hooks_path, "cursor_hook.config.missing"):
            return
        try:
            cursor_data = json.loads(self.read_text(cursor_hooks_path))
        except json.JSONDecodeError as exc:
            self.error("cursor_hook.config.invalid_json", f"{CURSOR_HOOKS_JSON} is not valid JSON: {exc}")
            return
        cursor_command_text = json.dumps(cursor_data, ensure_ascii=False).replace("\\", "/")
        for marker in ["preCompact", COMPACT_REMINDER_SCRIPT, "--agent cursor --event pre-compact"]:
            if marker not in cursor_command_text:
                self.error("cursor_hook.pre_compact.command", f"Cursor preCompact hook missing marker: {marker}")
        if script_path.exists():
            script_text = self.read_text(script_path)
            for needle in [
                "additionalContext",
                "user_message",
                "systemMessage",
                "workstate.json",
                "process.md",
                "design.md",
                "任务范围.md",
                "任务指标.md",
            ]:
                if needle not in script_text:
                    self.error("compact_hook.shared_script", f"{COMPACT_REMINDER_SCRIPT} missing recovery marker: {needle}")
        if not self.check_exists(claude_settings_path, "claude_hook.config.missing"):
            return
        try:
            claude_data = json.loads(self.read_text(claude_settings_path))
        except json.JSONDecodeError as exc:
            self.error("claude_hook.config.invalid_json", f"{CLAUDE_SETTINGS_JSON} is not valid JSON: {exc}")
            return
        claude_command_text = json.dumps(claude_data, ensure_ascii=False).replace("\\", "/")
        for marker in ["Stop", TURN_REMINDER_SCRIPT, "--agent claude --event stop", "--threshold 30"]:
            if marker not in claude_command_text:
                self.error("claude_hook.stop.command", f"Claude Stop hook missing marker: {marker}")
        if turn_script_path.exists():
            turn_script_text = self.read_text(turn_script_path)
            for needle in [
                '"decision": "block"',
                '"reason": message',
                "project-info-turn-reminder.json",
                "workstate.json",
                "process.md",
                "design.md",
                "任务范围.md",
                "任务指标.md",
                "DEFAULT_THRESHOLD = 30",
                "MIN_THRESHOLD = 20",
            ]:
                if needle not in turn_script_text:
                    self.error("turn_hook.shared_script", f"{TURN_REMINDER_SCRIPT} missing marker: {needle}")

    def check_public_roots(self) -> None:
        for relative in ["index.md", STARTUP, MAIN_FLOW, MAIN_RULES, DESIGN_HTML]:
            self.check_exists(self.info_root / relative, f"public.{relative}.missing")
        for relative in ["skills", "mcps", "hooks", "代码地图", "资料地图"]:
            self.check_exists(self.info_root / relative, f"public.{relative}.missing")
        self.check_exists(self.info_root / "代码地图" / "index.md", "public.代码地图.index.missing")
        self.check_exists(self.info_root / "资料地图" / "index.md", "public.资料地图.index.missing")
        for relative in ["协作灵魂.md", "沟通规范.md"]:
            if (self.info_root / relative).exists():
                self.error(
                    f"public.{relative}.private_only",
                    f"{relative} belongs in each author private user folder, not in {INFO_DIR}/",
                )

    def check_workstate(self) -> dict | None:
        path = self.repo_root / "workstate.json"
        if not path.exists():
            if self.allow_missing_workstate:
                self.warn("workstate.missing", "workstate.json is missing and was allowed")
                return None
            self.error("workstate.missing", "Missing root workstate.json")
            return None
        try:
            data = json.loads(self.read_text(path))
        except json.JSONDecodeError as exc:
            self.error("workstate.json", f"workstate.json is not valid JSON: {exc}")
            return None
        if not isinstance(data, dict):
            self.error("workstate.type", "workstate.json must contain a JSON object")
            return None
        author = data.get("author")
        task_folder = data.get("task_folder")
        if not isinstance(author, str) or not author.strip():
            self.error("workstate.author", "workstate.json author must be a non-empty string")
        if not isinstance(task_folder, str):
            self.error("workstate.task_folder", "workstate.json task_folder must be a string")
        return data

    def check_user_and_current_task(self, workstate: dict) -> None:
        author = workstate.get("author")
        if not isinstance(author, str) or not author.strip():
            return
        user_root = self.info_root / USER_DIR / author
        self.check_exists(user_root, "user.root.missing")
        for filename in USER_BASE_FILES:
            self.check_exists(user_root / filename, f"user.{filename}.missing")
        for dirname in USER_BASE_DIRS:
            self.check_exists(user_root / dirname, f"user.{dirname}.missing")
        self.check_exists(user_root / "代码地图" / "index.md", "user.代码地图.index.missing")
        self.check_exists(user_root / "代码地图" / "差异列表.md", "user.代码地图.diff.missing")
        self.check_exists(user_root / "资料地图" / "index.md", "user.资料地图.index.missing")

        task_folder = workstate.get("task_folder")
        if not isinstance(task_folder, str) or not task_folder.strip():
            self.warn("task.empty", "workstate.json task_folder is empty")
            return
        task_path = (self.repo_root / task_folder).resolve()
        expected_root = user_root.resolve()
        try:
            task_path.relative_to(expected_root)
        except ValueError:
            self.error("task.outside_user", f"Current task is outside current user area: {task_folder}")
            return
        for filename in CURRENT_TASK_FILES:
            self.check_exists(task_path / filename, f"task.{filename}.missing")
        scope = task_path / "任务范围.md"
        if scope.exists():
            self.check_task_scope(scope)

    def check_task_scope(self, path: Path) -> None:
        rows = self.extract_markdown_table_after_heading(self.read_text(path), "工作项")
        if not rows:
            self.error("task_scope.table", "任务范围.md must contain a table under ## 工作项")
            return
        headers = rows[0]
        if headers != TASK_SCOPE_COLUMNS:
            self.error(
                "task_scope.columns",
                "任务范围.md 工作项 table columns must be: " + ", ".join(TASK_SCOPE_COLUMNS),
            )
            return
        in_progress = 0
        items = [row for row in rows[1:] if not self.is_markdown_separator_row(row)]
        if not items:
            self.error("task_scope.empty", "任务范围.md 工作项 table must contain at least one item")
            return
        for index, row in enumerate(items, start=1):
            if len(row) != len(headers):
                self.error("task_scope.row", f"任务范围.md 工作项 row {index} has {len(row)} cells")
                continue
            item = dict(zip(headers, row, strict=True))
            for field in ["id", "标题", "状态", "验证方法"]:
                if not item.get(field):
                    self.error("task_scope.field", f"任务范围.md 工作项 row {index} missing {field}")
            status = item.get("状态", "")
            if status not in TASK_SCOPE_STATUSES:
                self.error("task_scope.status", f"任务范围.md 工作项 row {index} has invalid status: {status}")
            if status == "in_progress":
                in_progress += 1
        if in_progress > 1:
            self.error("task_scope.wip", "任务范围.md has more than one in_progress item")

    def extract_markdown_table_after_heading(self, text: str, heading: str) -> list[list[str]]:
        rows: list[list[str]] = []
        in_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                if in_section:
                    break
                in_section = stripped == f"## {heading}"
                continue
            if not in_section:
                continue
            if stripped.startswith("|") and stripped.endswith("|"):
                rows.append(self.split_markdown_row(stripped))
            elif rows and stripped:
                break
        return rows

    def split_markdown_row(self, line: str) -> list[str]:
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

    def is_markdown_separator_row(self, cells: list[str]) -> bool:
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)

    def check_public_index(self) -> None:
        index = self.info_root / "index.md"
        if not index.exists():
            return
        text = self.read_public_index_tree_text()
        for path in sorted(self.info_root.glob("*.md")):
            if path.name not in text:
                self.error("public_index.root_doc", f"Public root markdown not indexed: {path.name}")
        for skill_md in sorted((self.info_root / "skills").glob("*/SKILL.md")):
            relative = skill_md.parent.relative_to(self.info_root).as_posix() + "/"
            if relative not in text:
                self.error("public_index.skill", f"Public skill not indexed: {relative}")
        for relative in EXPECTED_PUBLIC_INDEX_PATHS:
            secondary_relative = posix_join("..", relative)
            if relative not in text and secondary_relative not in text:
                self.error("public_index.docs", f"Public docs path not indexed: {relative}")

    def read_public_index_tree_text(self) -> str:
        parts: list[str] = []
        root_index = self.info_root / "index.md"
        if root_index.exists():
            parts.append(self.read_text(root_index).replace("\\", "/"))
        secondary_root = self.info_root / "索引"
        if secondary_root.exists():
            for path in sorted(secondary_root.glob("*.md")):
                parts.append(self.read_text(path).replace("\\", "/"))
        skills_index = self.info_root / "skills" / "index.md"
        if skills_index.exists():
            parts.append(self.read_text(skills_index).replace("\\", "/"))
        skills_secondary = self.info_root / "skills" / "索引"
        if skills_secondary.exists():
            for path in sorted(skills_secondary.glob("*.md")):
                parts.append(self.read_text(path).replace("\\", "/"))
        return "\n".join(parts)

    def check_index_size(self) -> None:
        indexes = [self.info_root / "index.md"]
        users_root = self.info_root / USER_DIR
        if users_root.exists():
            indexes.extend(sorted(path / "index.md" for path in users_root.iterdir() if path.is_dir()))
        for index in indexes:
            if not index.exists():
                continue
            count = self.count_index_entries(index)
            if count > INDEX_MAX_ENTRIES:
                self.auto_split_oversized_indexes()
                return

    def auto_split_oversized_indexes(self) -> None:
        script = self.info_root / "skills" / "project-index-maintenance" / "scripts" / "check_index_size.py"
        if not script.exists():
            self.warn(
                "index.size.auto_split_unavailable",
                "First-level index exceeds the size threshold, but project-index-maintenance script is missing",
            )
            return
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo-root",
                str(self.repo_root),
                "--auto-split",
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        if completed.returncode != 0:
            self.error(
                "index.size.auto_split_failed",
                "Automatic index split failed: " + output,
            )
            return
        if output:
            self.warn("index.size.auto_split_applied", output)

    def count_index_entries(self, path: Path) -> int:
        count = 0
        for line in self.read_text(path).splitlines():
            stripped = line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                continue
            cells = self.split_markdown_row(stripped)
            if self.is_markdown_separator_row(cells):
                continue
            if not cells or cells[0] in {"文档", "Skill", "目录"}:
                continue
            if cells[0].startswith("`"):
                count += 1
        return count

    def check_external_skill_references(self) -> None:
        """External indexes should use project-info skill folders."""
        files = [
            self.info_root / "index.md",
            self.info_root / STARTUP,
            self.info_root / MAIN_FLOW,
            self.info_root / MAIN_RULES,
            self.info_root / "说明.md",
            self.info_root / DESIGN_HTML,
        ]
        users_root = self.info_root / USER_DIR
        if users_root.exists():
            files.extend(sorted(users_root.glob("*/index.md")))
        skill_md_pattern = re.compile(r"(?:项目大脑/)?skills/[^`\s)<>]+/SKILL\.md")
        for path in files:
            if not path.exists():
                continue
            text = self.read_text(path).replace("\\", "/")
            for match in skill_md_pattern.finditer(text):
                self.error(
                    "skill_reference.entry_file",
                    f"{self.rel(path)} references SKILL.md directly; use the project-info skill folder instead: {match.group(0)}",
                )

    def check_skill_indexes(self) -> None:
        skills_root = self.info_root / "skills"
        if not skills_root.exists():
            self.error("skills.root", "Missing public skills directory")
            return
        for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir() and path.name != "索引"):
            skill_md = skill_dir / "SKILL.md"
            if not self.check_exists(skill_md, f"skill.{skill_dir.name}.missing"):
                continue
            text = self.read_text(skill_md)
            for subdir_name in ["references", "examples", "scripts"]:
                subdir = skill_dir / subdir_name
                if not subdir.exists():
                    continue
                files = [
                    path
                    for path in subdir.rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
                ]
                for path in files:
                    relative = path.relative_to(skill_dir).as_posix()
                    if relative not in text and path.name not in text:
                        self.error("skill.orphan", f"{self.rel(path)} is not referenced by {self.rel(skill_md)}")

    def check_markdown_coverage(self) -> None:
        rules = self.info_root / MAIN_RULES
        if not rules.exists():
            return
        text = self.read_text(rules)
        artifact_text = self.read_artifact_taxonomy_text(text)
        artifact_cells = self.extract_artifact_cells(artifact_text)
        for name in EXPECTED_ARTIFACT_NAMES:
            if name not in artifact_cells:
                self.error("main_rules.artifact", f"Markdown artifact row missing: {name}")
        uncovered = self.find_uncovered_markdown(artifact_cells)
        for item in uncovered:
            self.error("markdown.coverage", f"Markdown file has no documented rule: {item}")

    def read_artifact_taxonomy_text(self, main_rules_text: str) -> str:
        reference = self.info_root / ARTIFACT_TAXONOMY_REFERENCE
        marker = "project-info-artifact-taxonomy"
        if reference.exists():
            if marker not in main_rules_text:
                self.error(
                    "main_rules.artifact_taxonomy",
                    f"{MAIN_RULES} must reference {ARTIFACT_TAXONOMY_REFERENCE}",
                )
            return self.read_text(reference)
        return main_rules_text

    def extract_artifact_cells(self, text: str) -> set[str]:
        match = re.search(r"## Markdown 工件\n(.+?)\n## ", text, flags=re.S)
        section = match.group(1) if match else text
        cells: set[str] = set()
        for line in section.splitlines():
            if not line.startswith("|") or line.startswith("| ---") or line.startswith("| 文件名"):
                continue
            parts = [part.strip() for part in line.strip("|").split("|")]
            if parts and parts[0] != "文件名":
                cells.add(parts[0].strip("`"))
        return cells

    def find_uncovered_markdown(self, artifact_cells: set[str]) -> list[str]:
        uncovered: list[str] = []
        root_entries = {Path(name) for name in ROOT_ENTRY_FILES}
        candidates: list[Path] = [*root_entries]
        candidates.extend(path.relative_to(self.repo_root) for path in self.info_root.rglob("*.md"))
        for relative in sorted(candidates, key=lambda path: str(path).lower()):
            if not self.is_markdown_covered(relative, artifact_cells):
                uncovered.append(relative.as_posix())
        return uncovered

    def is_markdown_covered(self, relative: Path, artifact_cells: set[str]) -> bool:
        parts = relative.parts
        if relative in {Path(name) for name in ROOT_ENTRY_FILES}:
            return relative.name in artifact_cells
        if len(parts) >= 2 and parts[0] == INFO_DIR and parts[1] == "skills":
            if relative.name == "SKILL.md":
                skill_folder = "/".join(parts[1:-1]) + "/"
                return skill_folder in self.read_public_index_tree_text()
            if "references" in parts or "examples" in parts:
                skill_md = self.repo_root / Path(*parts[:3]) / "SKILL.md"
                if not skill_md.exists():
                    return False
                text = self.read_text(skill_md)
                return "/".join(parts[3:]) in text or relative.name in text
        if len(parts) == 2 and parts[0] == INFO_DIR:
            if relative.name == STARTUP:
                return relative.name in self.read_public_index_tree_text()
            key = relative.name if relative.name != "index.md" else f"{INFO_DIR}/index.md"
            return key in artifact_cells or relative.name == MAIN_FLOW
        if len(parts) >= 3 and parts[0] == INFO_DIR and parts[1] == "索引":
            if "索引/" not in artifact_cells:
                return False
            index = self.info_root / "index.md"
            if not index.exists():
                return False
            return "/".join(parts[1:]) in self.read_text(index).replace("\\", "/")
        if len(parts) == 3 and parts[0] == INFO_DIR and parts[1] == "skills" and relative.name == "index.md":
            root_index = self.info_root / "index.md"
            if not root_index.exists():
                return False
            return "skills/index.md" in self.read_text(root_index).replace("\\", "/")
        if len(parts) >= 4 and parts[0] == INFO_DIR and parts[1] == "skills" and parts[2] == "索引":
            skill_index = self.info_root / "skills" / "index.md"
            if not skill_index.exists():
                return False
            return "/".join(parts[1:]) in self.read_text(skill_index).replace("\\", "/") or "/".join(parts[2:]) in self.read_text(skill_index).replace("\\", "/")
        if len(parts) >= 3 and parts[0] == INFO_DIR and parts[1] == "公理":
            if "公理/" not in artifact_cells:
                return False
            axiom_index = self.info_root / "公理" / "index.md"
            if relative.name == "index.md":
                return axiom_index.exists()
            if not axiom_index.exists():
                return False
            return relative.name in self.read_text(axiom_index)
        if len(parts) >= 3 and parts[0] == INFO_DIR and parts[1] == "代码地图":
            if "代码地图/" not in artifact_cells:
                return False
            code_map_index = self.info_root / "代码地图" / "index.md"
            if relative.name == "index.md":
                return code_map_index.exists()
            if not code_map_index.exists():
                return False
            code_map_path = "/".join(parts[2:])
            return code_map_path in self.read_text(code_map_index).replace("\\", "/") or relative.name in self.read_text(code_map_index)
        if len(parts) >= 3 and parts[0] == INFO_DIR and parts[1] == "资料地图":
            if "资料地图/" not in artifact_cells:
                return False
            resource_map_index = self.info_root / "资料地图" / "index.md"
            if relative.name == "index.md":
                return resource_map_index.exists()
            if not resource_map_index.exists():
                return False
            resource_map_path = "/".join(parts[2:])
            return resource_map_path in self.read_text(resource_map_index).replace("\\", "/") or relative.name in self.read_text(resource_map_index)
        if len(parts) >= 4 and parts[0] == INFO_DIR and parts[1] == USER_DIR:
            if "tasks" in parts:
                task_dir = self.repo_root / Path(*parts[:5])
                reference_text = self.read_task_reference_text(task_dir)
                task_relative = "/".join(parts[5:])
                if relative.name.startswith("给") and relative.name.endswith("的对齐说明.md"):
                    return TASK_ALIGNMENT_ARTIFACT in artifact_cells
                if TASK_DATED_NOTE_PATTERN.match(relative.name):
                    return TASK_DATED_NOTE_ARTIFACT in artifact_cells and relative.name in reference_text
                if relative.name in KNOWN_TASK_MD:
                    return relative.name in artifact_cells
                return bool(reference_text) and (task_relative in reference_text or relative.name in reference_text)
            if len(parts) >= 5 and parts[3] == "收件":
                return (
                    "收件/" in artifact_cells
                    and TASK_DATED_NOTE_ARTIFACT in artifact_cells
                    and TASK_DATED_NOTE_PATTERN.match(relative.name) is not None
                )
            if len(parts) >= 5 and parts[3] == "skills":
                # 私有 skill：SKILL.md 由该用户私人 index.md 以文件夹形式索引；
                # 其余 md 由所属 SKILL.md 按相对路径、文件名或上级目录索引。
                skill_md = self.repo_root / Path(*parts[:5]) / "SKILL.md"
                if relative.name == "SKILL.md" and len(parts) == 6:
                    user_index = self.repo_root / Path(*parts[:3]) / "index.md"
                    if not user_index.exists():
                        return False
                    index_text = self.read_text(user_index).replace("\\", "/")
                    return f"skills/{parts[4]}/" in index_text
                if not skill_md.exists():
                    return False
                skill_text = self.read_text(skill_md).replace("\\", "/")
                inner_path = "/".join(parts[5:])
                if inner_path in skill_text or relative.name in skill_text:
                    return True
                parent = posixpath.dirname(inner_path)
                while parent:
                    if f"{parent}/" in skill_text:
                        return True
                    parent = posixpath.dirname(parent)
                return False
            if "代码地图" in parts:
                if "代码地图/" not in artifact_cells:
                    return False
                code_map_index = self.repo_root / Path(*parts[:4]) / "index.md"
                if relative.name == "index.md":
                    return code_map_index.exists()
                if not code_map_index.exists():
                    return False
                code_map_path = "/".join(parts[4:])
                return code_map_path in self.read_text(code_map_index).replace("\\", "/") or relative.name in self.read_text(code_map_index)
            if "资料地图" in parts:
                if "资料地图/" not in artifact_cells:
                    return False
                resource_map_index = self.repo_root / Path(*parts[:4]) / "index.md"
                if relative.name == "index.md":
                    return resource_map_index.exists()
                if not resource_map_index.exists():
                    return False
                resource_map_path = "/".join(parts[4:])
                return resource_map_path in self.read_text(resource_map_index).replace("\\", "/") or relative.name in self.read_text(resource_map_index)
            if "经验" in parts:
                user_index = self.repo_root / Path(*parts[:3]) / "index.md"
                if not user_index.exists():
                    return False
                index_text = self.read_text(user_index).replace("\\", "/")
                experience_path = "/".join(parts[3:])
                return experience_path in index_text
            if len(parts) >= 5 and parts[3] == "索引":
                if "索引/" not in artifact_cells:
                    return False
                user_index = self.repo_root / Path(*parts[:3]) / "index.md"
                if not user_index.exists():
                    return False
                index_text = self.read_text(user_index).replace("\\", "/")
                secondary_path = "/".join(parts[3:])
                return secondary_path in index_text
            key = f"{USER_DIR}/{{author}}/index.md" if relative.name == "index.md" else relative.name
            return key in artifact_cells
        return False

    def read_task_reference_text(self, task_dir: Path) -> str:
        return "\n".join(
            self.read_text(task_dir / name)
            for name in CURRENT_TASK_FILES
            if (task_dir / name).exists()
        )

    def check_design_html(self) -> None:
        path = self.info_root / DESIGN_HTML
        if not path.exists():
            return
        text = self.read_text(path)
        for name in [*EXPECTED_ARTIFACT_NAMES, "project-info-healthcheck"]:
            if name not in text:
                self.warn("html.sync", f"说明.html does not mention {name}")

    def check_topic_cluster_documents(self) -> None:
        axiom_root = self.info_root / "公理"
        if axiom_root.exists():
            for path in sorted(axiom_root.glob("*.md")):
                if path.name != "index.md":
                    self.check_topic_cluster_filename(path, "axiom.topic_filename")

        users_root = self.info_root / USER_DIR
        if not users_root.exists():
            return
        for user_root in sorted(path for path in users_root.iterdir() if path.is_dir()):
            experience_root = user_root / "经验"
            if not experience_root.exists():
                continue
            for path in sorted(experience_root.glob("*.md")):
                self.check_topic_cluster_filename(path, "experience.topic_filename")

    def check_topic_cluster_filename(self, path: Path, code: str) -> None:
        if SINGLE_ITEM_TOPIC_FILE_PATTERN.search(path.stem):
            self.error(
                code,
                f"{self.rel(path)} looks like a single-item document; use a stable topic-cluster filename instead",
            )

    def check_code_map_line_references(self) -> None:
        roots = [self.info_root / "代码地图", self.info_root / "资料地图"]
        users_root = self.info_root / USER_DIR
        if users_root.exists():
            roots.extend(path / "代码地图" for path in users_root.iterdir() if path.is_dir())
            roots.extend(path / "资料地图" for path in users_root.iterdir() if path.is_dir())
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.md")):
                text = self.read_text(path).replace("\\", "/")
                match = CODE_MAP_LINE_REF_PATTERN.search(text)
                if match:
                    self.error(
                        "code_map.line_reference",
                        f"{self.rel(path)} contains a line-number style code reference: {match.group(0)}",
                    )

    def check_main_flow_does_not_reverse_reference_startup(self) -> None:
        path = self.info_root / MAIN_FLOW
        if not path.exists():
            return
        text = self.read_text(path)
        for forbidden in [STARTUP, f"{INFO_DIR}/{STARTUP}"]:
            if forbidden in text:
                self.error(
                    "main_flow.reverse_startup_reference",
                    f"{MAIN_FLOW} is loaded by {STARTUP}; do not reference {forbidden} from the main flow",
                )

    def check_main_flow_runtime_hub(self) -> None:
        path = self.info_root / MAIN_FLOW
        if not path.exists():
            return
        text = self.read_text(path).replace("\\", "/")
        for marker in [
            "硬规则速查",
            "条件加载总表",
            "重载与防跑偏",
            "不读 `主规则.md` 全文",
            "project-inbox-rules",
            "curation-hard-rules",
            "提交",
            "推送",
            "math-sense",
        ]:
            if marker not in text:
                self.error(
                    "main_flow.runtime_hub",
                    f"{MAIN_FLOW} missing Runtime Hub marker: {marker}",
                )
        rules = self.info_root / MAIN_RULES
        if rules.exists():
            rules_text = self.read_text(rules).replace("\\", "/")
            if "冷启动不读本文件全文" not in rules_text:
                self.error(
                    "main_rules.detail_pack",
                    f"{MAIN_RULES} must say cold start does not read the full rules file",
                )
            if "主流程.md` §2" not in rules_text and "主流程.md` §2 硬规则速查" not in rules_text:
                self.error(
                    "main_rules.detail_pack",
                    f"{MAIN_RULES} must point cold-start readers to {MAIN_FLOW} section 2 hard-rule quick reference",
                )

    def check_private_extension_default_rules(self) -> None:
        paths = [
            self.repo_root / "AGENTS.md",
            self.info_root / STARTUP,
            self.info_root / MAIN_RULES,
            self.info_root / "skills" / "project-info-extension-standards" / "SKILL.md",
        ]
        for path in paths:
            if not path.exists():
                continue
            text = self.read_text(path)
            if PRIVATE_EXTENSION_DEFAULT_MARKER not in text or LEADER_PUBLICIZATION_MARKER not in text:
                self.error(
                    "extension.private_default",
                    f"{self.rel(path)} must say new skill/MCP/hook artifacts default to the current author private area and public writes use leader publicization",
                )

    def check_user_private_boundary_rules(self) -> None:
        users_root = self.info_root / USER_DIR
        if not users_root.exists():
            return
        for user_root in sorted(path for path in users_root.iterdir() if path.is_dir()):
            self.check_user_private_index(user_root)
            responsibility = user_root / "职责范围.md"
            if not responsibility.exists():
                continue
            text = self.read_text(responsibility)
            if PRIVATE_USER_BOUNDARY_MARKER not in text:
                self.error(
                    "user.responsibility.private_boundary",
                    f"{self.rel(responsibility)} must forbid modifying other author private user folders",
                )
            if RESPONSIBILITY_SCOPE_REFUSAL_MARKER not in text or RESPONSIBILITY_SCOPE_RISK_MARKER not in text:
                self.error(
                    "user.responsibility.scope_refusal",
                    f"{self.rel(responsibility)} must require refusing out-of-scope file changes and explaining risks",
                )
            if RESPONSIBILITY_SCOPE_SYNC_MARKER not in text or RESPONSIBILITY_SCOPE_DELIVERY_MARKER not in text:
                self.error(
                    "user.responsibility.scope_sync",
                    f"{self.rel(responsibility)} must allow current author to sync agreed delivery-rule documents into their own scope",
                )
            if RESPONSIBILITY_PRIVATE_EXTENSION_MARKER not in text:
                self.error(
                    "user.responsibility.private_extension_default",
                    f"{self.rel(responsibility)} must require new skill/MCP/hook artifacts to default to the current author private area",
                )

    def check_experience_curation_rules(self) -> None:
        main_flow = self.info_root / MAIN_FLOW
        main_rules = self.info_root / MAIN_RULES
        curation_skill = self.info_root / "skills" / "personal-experience-curation" / "SKILL.md"
        curation_hard_rules = self.info_root / CURATION_HARD_RULES_REFERENCE
        evaluation_ref = (
            self.info_root
            / "skills"
            / "personal-experience-curation"
            / "references"
            / "experience-evaluation.md"
        )
        standards_ref = (
            self.info_root
            / "skills"
            / "personal-experience-curation"
            / "references"
            / "experience-curation-standards.md"
        )
        inbox_skill = self.info_root / INBOX_RULES_SKILL / "SKILL.md"
        old_skill = self.info_root / "skills" / "periodic-experience-curation"
        if old_skill.exists():
            self.error(
                "experience_curation.old_skill",
                f"Remove deprecated mixed skill directory: {self.rel(old_skill)}",
            )
        for deprecated in DEPRECATED_PUBLIC_SKILLS:
            path = self.info_root / "skills" / deprecated
            if path.exists():
                self.error(
                    "experience_curation.deprecated_skill",
                    f"Remove deprecated public skill directory: {self.rel(path)}",
                )
        for path, markers in [
            (
                main_flow,
                [
                    "personal-experience-curation",
                    "curation-hard-rules",
                    "project-inbox-rules",
                ],
            ),
            (
                main_rules,
                [
                    "personal-experience-curation",
                    "curation-hard-rules",
                    "project-inbox-rules",
                    "任务留痕",
                    "perfect-push",
                    "冷启动不读本文件全文",
                ],
            ),
            (
                curation_hard_rules,
                [
                    "积累触发",
                    "整理确认.md",
                    "L0 任务过程",
                    "L3 硬规则",
                    "personal-experience-curation",
                ],
            ),
            (inbox_skill, ["header-format.md", "startup-scan.md", "design-advisor-lens"]),
            (
                curation_skill,
                ["curation-rules.md", "entry-template.md", "经验/entries"],
            ),
            (evaluation_ref, ["评分表", "硬拒绝", "前因后果"]),
            (
                standards_ref,
                [
                    "未来 3 个月",
                    "证据闭环",
                    "L0 任务过程",
                    "L1 私人经验",
                    "L3 硬规则",
                ],
            ),
        ]:
            if not path.exists():
                self.error("experience_curation.path", f"Missing experience curation path: {self.rel(path)}")
                continue
            text = self.read_text(path).replace("\\", "/")
            for marker in markers:
                if marker not in text:
                    self.error(
                        "experience_curation.marker",
                        f"{self.rel(path)} missing experience curation marker: {marker}",
                    )
        users_root = self.info_root / USER_DIR
        if not users_root.exists():
            return
        for user_root in sorted(path for path in users_root.iterdir() if path.is_dir()):
            old_state = user_root / "经验" / "整理状态.md"
            if old_state.exists():
                self.error(
                    "user.experience_curation.old_state",
                    f"Remove deprecated mixed curation state: {self.rel(old_state)}",
                )

    def check_user_private_index(self, user_root: Path) -> None:
        index = user_root / "index.md"
        if not index.exists():
            return
        text = self.read_text(index)
        for forbidden in PRIVATE_INDEX_FORBIDDEN_DEFAULT_SECTIONS:
            if forbidden in text:
                self.error(
                    "user.index.default_public_section",
                    f"{self.rel(index)} must not duplicate default public entry sections: {forbidden}",
                )


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(f"Cannot find repo root from {start}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check 项目大脑 consistency.")
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument(
        "--allow-missing-workstate",
        action="store_true",
        help="Report missing workstate.json as a warning instead of an error.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    return Healthcheck(repo_root, args.allow_missing_workstate).run()


if __name__ == "__main__":
    raise SystemExit(main())
