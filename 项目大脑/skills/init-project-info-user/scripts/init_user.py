from __future__ import annotations

"""Initialize a base 项目大脑 user workspace.

Usage:
    python 项目大脑/skills/init-project-info-user/scripts/init_user.py --author {author}
"""

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from author_aliases import resolve_canonical_author


AUTHOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PRIVATE_FLOW_FILENAME = "私有流程.md"
PRIVATE_RULES_FILENAME = "私有规则.md"
RESPONSIBILITY_FILENAME = "职责范围.md"
INDEX_FILENAME = "index.md"
USER_PROFILE_FILENAME = "用户画像.md"
WORK_HABITS_FILENAME = "工作习惯.md"
COLLABORATION_SOUL_FILENAME = "协作灵魂.md"
COMMUNICATION_NORMS_FILENAME = "沟通规范.md"
COMMUNICATION_PREFERENCES_FILENAME = "沟通偏好.md"
TASKS_DIRNAME = "tasks"
EXPERIENCE_DIRNAME = "经验"
PRIVATE_EXPERIENCE_CURATION_STATE_FILENAME = "私人整理状态.md"
LEADER_CURATION_STATE_FILENAME = "leader整理状态.md"
LEADER_AUTHOR = "tech-lead"
CODE_MAP_DIRNAME = "代码地图"
CODE_MAP_DIFF_FILENAME = "差异列表.md"
RESOURCE_MAP_DIRNAME = "资料地图"
PRIVATE_EXTENSION_DIRNAMES = ("skills", "mcps", "hooks")
NONTECH_SAFETY_GUARDRAIL_PATH = "项目大脑/非技术人员操作安全护栏.md"


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


def user_root(repo_root: Path, author: str) -> Path:
    return repo_root / "项目大脑" / "用户" / author


def normalize_relative_path(repo_root: Path, path: Path, *, trailing_slash: bool = False) -> str:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path is outside repo: {path}") from exc
    normalized = relative.as_posix().rstrip("/")
    return normalized + "/" if trailing_slash else normalized


def write_gitkeep_if_empty(path: Path) -> None:
    marker = path / ".gitkeep"
    if marker.exists():
        return
    if any(path.iterdir()):
        return
    marker.write_text("keep directory\n", encoding="utf-8")


def read_initial_assignment(repo_root: Path, author: str) -> dict[str, str] | None:
    assignment_path = repo_root / "项目大脑" / "初始分工表.md"
    if not assignment_path.exists():
        return None

    text = assignment_path.read_text(encoding="utf-8-sig")
    headers: list[str] = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == "成员":
            headers = cells
            continue
        if not headers or f"`{author}`" not in line:
            continue
        row = dict(zip(headers, cells, strict=False))
        return {
            "member": row.get("成员", ""),
            "nickname": row.get("昵称 / 转写别名", "待确认"),
            "role_type": row.get("角色类型", "待确认"),
            "account": row.get("账号名 / 提交别名", ""),
            "observation": row.get("提交观察", ""),
            "responsibility": row.get("主要开发分工", ""),
            "boundary": row.get("修改边界", ""),
            "collaboration": row.get("需要协作的相邻区域", ""),
        }
    return None


def should_load_nontech_safety_guardrail(author: str, assignment: dict[str, str] | None) -> bool:
    role_type = assignment["role_type"] if assignment else ""
    if author in ("team-lead", "tech-lead"):
        return False
    if "程序" in role_type or "技术策划" in role_type:
        return False
    return True


def build_responsibility_text(author: str, assignment: dict[str, str] | None) -> str:
    nickname = assignment["nickname"] if assignment else "待当前 author 补充。"
    role_type = assignment["role_type"] if assignment else "待当前 author 补充。"
    responsibility = assignment["responsibility"] if assignment else "待当前 author 补充。"
    boundary = assignment["boundary"] if assignment else "待当前 author 补充。"
    collaboration = assignment["collaboration"] if assignment else "待当前 author 补充。"
    observation = assignment["observation"] if assignment else "未在 `初始分工表.md` 中匹配到当前 author。"
    leader_permission = (
        "- `tech-lead` 是程序 leader，可以修改 `项目大脑/启动.md`、`项目大脑/主流程.md` 和 `项目大脑/index.md`。\n"
        "- `tech-lead` 拥有审查、合并、总结，以及把个人/任务经验提升到项目大脑公共区的权限。"
        if author == "tech-lead"
        else "- `项目大脑/启动.md`、`项目大脑/主流程.md` 和 `项目大脑/index.md` 只允许 `tech-lead` 修改；当前 author 如需调整，应在当前任务 `process.md` 记录建议并交给 `tech-lead` 收口。"
    )

    return (
        "# 职责范围\n\n"
        f"> author: `{author}`\n"
        f"> 角色类型: `{role_type}`\n"
        "> 初始化来源: `项目大脑/初始分工表.md`\n"
        "> 状态: 初版；当前 author 可以维护本文件，主流程按本文件控制默认修改边界。\n\n"
        "## 使用规则\n\n"
        "- 主流程在准备修改文件前读取本文件。\n"
        "- `项目大脑/初始分工表.md` 只用于初始化或人工修订参考，日常边界以本文件为准。\n"
        "- 所有 author 都不得直接修改其他 author 的 `项目大脑/用户/{other-author}/` 私有用户区；跨角色规则交付由对方确认后装载到自己的私人区。\n"
        "- 新建 skill、MCP、hook 或辅助落盘默认先放当前 author 私有区；公共区只由 `tech-lead` 通过 leader 公共化流程维护。\n"
        "- 当前 author 可以主动同步已达成共识的交付规则文档到自己的 `职责范围.md`；同步时写清来源文档、允许范围、禁止事项、生效时间和风险。\n"
        "- 准备修改的文件明显不在职责范围内时，先明确拒绝本次越界修改，并说明风险；如果用户坚持，先询问是否要修改当前 author 的职责范围/修改边界，获得明确确认并在当前任务 `process.md` 记录风险后，才能更新边界或继续。\n"
        "- 当前 author 可以修改自己的职责范围；修改时写清原因，避免边界变化不可追踪。\n\n"
        "## 主要职责\n\n"
        f"- {responsibility}\n\n"
        "## 允许修改区域\n\n"
        f"- {boundary}\n\n"
        "## 需要协作的相邻区域\n\n"
        f"- {collaboration}\n\n"
        "## 受保护文件权限\n\n"
        f"{leader_permission}\n\n"
        "## 自我维护规则\n\n"
        "- 当前 author 可以修改本文件，以反映真实职责变化。\n"
        "- 扩大修改边界、同步交付规则或调整受保护文件权限时，要写清原因、来源和风险，并在当前任务 `process.md` 记录。\n"
        "- 不用本文件绕过模块 `ARCHITECTURE.md`、`CONSTRAINTS.md`、代码规范或用户明确边界。\n\n"
        "## 昵称 / 转写别名\n\n"
        f"- {nickname}\n\n"
        "## 角色类型\n\n"
        f"- {role_type}\n\n"
        "## 初始化观察\n\n"
        f"- {observation}\n"
    )


def build_user_profile_text(author: str, assignment: dict[str, str] | None) -> str:
    member = assignment["member"] if assignment else "待当前 author 补充。"
    nickname = assignment["nickname"] if assignment else "待当前 author 补充。"
    role_type = assignment["role_type"] if assignment else "待当前 author 补充。"
    account = assignment["account"] if assignment else f"`{author}`"
    responsibility = assignment["responsibility"] if assignment else "待当前 author 补充。"
    observation = assignment["observation"] if assignment else "未在 `初始分工表.md` 中匹配到当前 author。"

    return (
        "# 用户画像\n\n"
        f"> author: `{author}`\n"
        "> 用途: 记录当前 author 的长期稳定背景、职责身份、昵称和协作上下文，帮助 agent 正确理解人名、转写别名和沟通语境。\n"
        "> 状态: 初始化模板；只写已确认事实，不猜测性格。\n\n"
        "## 使用规则\n\n"
        "- 本文件是私人用户区的软上下文，不替代 `职责范围.md`、代码规范、主规则或用户明确指令。\n"
        "- 只写长期稳定、可复用的信息；临时任务状态写入当前任务 `process.md`。\n"
        "- 从会议录音 AI 转写、聊天记录或提交历史补充信息时，要能说明来源或上下文。\n"
        "- 不写密码、密钥、私人联系方式、未公开隐私信息或无法确认的推断。\n\n"
        "## 基本信息\n\n"
        "| 项 | 内容 |\n"
        "| --- | --- |\n"
        f"| 成员 | {member} |\n"
        f"| author | `{author}` |\n"
        f"| 角色类型 | {role_type} |\n"
        f"| 账号名 / 提交别名 | {account} |\n"
        f"| 昵称 / 转写别名 | {nickname} |\n"
        "| 职责入口 | `职责范围.md` |\n\n"
        "## 背景与职责摘要\n\n"
        f"- {responsibility}\n\n"
        "## 识别线索\n\n"
        f"- {observation}\n\n"
        "## 待补充\n\n"
        "- 常见表达习惯。\n"
        "- 常用判断标准。\n"
        "- 会议转写中容易混淆的人名或别名。\n"
    )


def build_work_habits_text(author: str, assignment: dict[str, str] | None) -> str:
    responsibility = assignment["responsibility"] if assignment else "待当前 author 补充。"

    return (
        "# 工作习惯\n\n"
        f"> author: `{author}`\n"
        "> 用途: 记录当前 author 长期稳定的工作方式、验收偏好和协作节奏，帮助 agent 选择更贴近本人的执行路径。\n"
        "> 状态: 初始化模板；先放结构，后续由当前 author 逐步补充。\n\n"
        "## 使用规则\n\n"
        "- 本文件记录长期稳定的个人工作习惯，不写单个任务流水。\n"
        "- 任务过程、阶段验收、风险和下一步写入当前任务 `process.md`。\n"
        "- 稳定设计决策写入当前任务 `design.md`；多次验证后可由 leader 提升到公共区。\n"
        "- 习惯不能覆盖主规则、代码规范、模块约束或用户当次明确要求。\n\n"
        "## 默认工作方式\n\n"
        "| 场景 | 默认做法 | 例外 |\n"
        "| --- | --- | --- |\n"
        "| 开始任务 | 先读公共主流程，再读 `私有流程.md`、`私有规则.md` 和当前任务上下文；`index.md`、`职责范围.md`、`tasklist.md` 按需读取。 | 用户明确要求只回答问题时，不创建任务文件。 |\n"
        "| 修改代码 | 先查相关规范、模块边界和相似实现，再小步改动并验证触达功能。 | 紧急修复可缩短调研，但要在 `process.md` 写清风险。 |\n"
        "| 写文档 | 先更新权威正文，再让索引只保留链接和一句话用途。 | 用户要求评审稿或演示稿时，可单独生成 HTML。 |\n"
        "| 收尾 | 更新 `process.md`，必要时更新 `design.md`、`tasklist.md` 和验证证据。 | 用户明确暂停时，至少写清当前状态和下一步。 |\n\n"
        "## 当前职责相关习惯\n\n"
        f"- {responsibility}\n\n"
        "## 待补充\n\n"
        "- 本人常用测试方式。\n"
        "- 本人偏好的阶段粒度。\n"
        "- 本人常用工具链和容易踩坑的工具。\n"
    )


def build_communication_preferences_text(author: str, assignment: dict[str, str] | None) -> str:
    nickname = assignment["nickname"] if assignment else "待当前 author 补充。"

    return (
        "# 沟通偏好\n\n"
        f"> author: `{author}`\n"
        "> 用途: 记录当前 author 与 agent 协作时的稳定沟通偏好，尤其用于报告、交接、会议转写和别名识别。\n"
        "> 状态: 初始化模板；只记录已确认偏好。\n\n"
        "## 使用规则\n\n"
        "- 本文件是沟通软上下文，不替代用户当次明确指令。\n"
        "- 用户当次要求和私人 `沟通规范.md` 冲突时，以当次要求为准；常态偏好可以回写本文件。\n"
        "- 会议录音 AI 转写里的昵称、错别字和简称可以补到本文件，帮助后续正确对应人员。\n"
        "- 不复制长对话原文，只记录可复用结论和识别规则。\n\n"
        "## 称呼与别名\n\n"
        "| 类型 | 内容 |\n"
        "| --- | --- |\n"
        f"| author | `{author}` |\n"
        f"| 昵称 / 转写别名 | {nickname} |\n\n"
        "## 默认沟通方式\n\n"
        "| 场景 | 偏好 |\n"
        "| --- | --- |\n"
        "| 状态更新 | 先说正在做什么、发现了什么、下一步是什么。 |\n"
        "| 风险报告 | 直接说明问题、影响范围、已验证证据和建议处理方式。 |\n"
        "| 收尾汇报 | 简短列出改了什么、验证了什么、是否还有未完成风险。 |\n"
        "| 设计讨论 | 先给结论和理由，再列可选方案；不把过程文档当成待执行任务散落。 |\n\n"
        "## 待补充\n\n"
        "- 本人常用缩写。\n"
        "- 本人不喜欢的报告方式。\n"
        "- 会议转写中常见错别字和对应关系。\n"
    )


def build_collaboration_soul_text(author: str) -> str:
    return (
        "# 协作灵魂\n\n"
        f"> author: `{author}`\n"
        "> 用途: 记录当前 author 与 agent 协作时的软基调。它借鉴“预制菜”里 SOUL 类文档的结构，但只写当前 author 已确认、可复用的协作方式。\n\n"
        "本文件不是硬规则。硬规则以 `AGENTS.md`、`项目大脑/主规则.md`、代码规范、模块约束和用户当次明确要求为准。"
        "它的作用是让 agent 面对当前 author 时表现得更稳定、更可预期。\n\n"
        "## 使用规则\n\n"
        "- 当任务涉及协作方式、角色边界、报告风格、长期记忆整理时，按需查看本文件。\n"
        "- 不把本文件当成任务清单；具体任务过程写当前任务 `process.md`。\n"
        "- 不写无法确认的私人心理画像，不替代 `用户画像.md`、`工作习惯.md` 或 `职责范围.md`。\n"
        "- 只维护当前 author 的协作基调；不写其他 author 的私人协作要求。\n\n"
        "## 核心基调\n\n"
        "| 基调 | 含义 | 行为表现 |\n"
        "| --- | --- | --- |\n"
        "| 可靠胜过表演 | 先把事实、风险、验证和边界讲清楚。 | 不用空泛赞美，不用模糊承诺，不把未验证内容说成完成。 |\n"
        "| 先找已有上下文 | 新功能、新文档、新流程先看现有规范、索引和相似实现。 | 优先读公共主流程、`私有流程.md`、代码规范、模块文档和 task 记录；需要私人文献时再查询 `index.md`。 |\n"
        "| 小步可回滚 | 修改应按职责边界和任务范围控制，避免一次性铺太大。 | 同类批量修改可以合并，但每阶段要能验收。 |\n"
        "| 问题及时暴露 | 外部失败、框架错误、测试异常要输出有用信息。 | 简单问题可修；复杂框架崩溃要停下来报告。 |\n"
        "| 尊重多人边界 | 每个人有自己的 author 区、任务区、职责边界和常用文档索引。 | 不把私人过程写进公共入口，不跨职责范围改动。 |\n"
        "| 公共化要克制 | 只有稳定、复用、团队有价值的内容才建议提升到公共区。 | 私人经验先沉淀，leader 周期整理，合并前征求相关人意见。 |\n\n"
        "## 与私人文档的关系\n\n"
        "| 文档 | 边界 |\n"
        "| --- | --- |\n"
        "| `协作灵魂.md` | 当前 author 与 agent 的协作软基调。 |\n"
        "| `用户画像.md` | 当前 author 的身份、昵称、长期背景和识别上下文。 |\n"
        "| `工作习惯.md` | 当前 author 的个人工作方式、验收偏好和节奏。 |\n"
        "| `沟通规范.md` | 当前 author 与 agent 的稳定沟通规范。 |\n"
        "| `沟通偏好.md` | 当前 author 的沟通偏好、称呼、会议转写别名和细节偏好。 |\n\n"
        "## 维护规则\n\n"
        "- 新增条目应来自真实协作复盘、稳定用户偏好或用户明确确认。\n"
        "- 引用外部结构时只写“借鉴了什么结构”，不复制外部私人内容。\n"
        "- 如果本文件和用户当次要求冲突，以用户当次要求为准；事后再判断是否需要更新本文件。\n"
    )


def build_communication_norms_text(author: str) -> str:
    return (
        "# 沟通规范\n\n"
        f"> author: `{author}`\n"
        "> 用途: 记录当前 author 与 agent 协作时的稳定沟通规范，帮助 agent 写状态更新、收尾汇报、风险报告、会议整理和文档说明。\n\n"
        "本文件是当前 author 私有沟通约定。称呼、昵称、会议转写别名和更细的个人偏好写在 `沟通偏好.md`，具体任务过程写在当前任务 `process.md`。\n\n"
        "## 速查\n\n"
        "| 场景 | 做法 |\n"
        "| --- | --- |\n"
        "| 工作中状态更新 | 说明正在做什么、发现了什么、下一步是什么。 |\n"
        "| 发现风险 | 先说风险和影响，再说证据、已尝试动作和建议。 |\n"
        "| 收尾汇报 | 简短说明改动、验证、未完成风险和是否需要用户决定。 |\n"
        "| 文档引用 | 只放链接和一句话用途，不复制权威正文。 |\n"
        "| 会议转写整理 | 保留结论、行动项、争议和人名映射；不把原始长文塞进公共入口。 |\n"
        "| 需要用户确认 | 直接问阻塞问题，说明为什么必须确认。 |\n\n"
        "## 基本风格\n\n"
        "- 默认使用简体中文。\n"
        "- 先结论，后证据；复杂问题再给方案对比。\n"
        "- 不把“我觉得”“应该没问题”当验收结论，要给可验证证据。\n"
        "- 不用行话堆叠掩盖不确定性；不确定就说明不确定和下一步验证方法。\n"
        "- 不在索引、启动文件、主规则里复制其他文档正文，避免一处改动多处同步。\n"
        "- 不把过程文档写成新的待执行任务，避免其他 agent 误启动旧工作。\n\n"
        "## 报告格式\n\n"
        "| 类型 | 建议结构 |\n"
        "| --- | --- |\n"
        "| 状态更新 | 当前进展；发现；下一步。 |\n"
        "| 风险报告 | 问题；影响范围；证据；建议处理；是否阻塞。 |\n"
        "| 收尾汇报 | 改了什么；验证了什么；还有什么风险；是否已提交/是否待提交。 |\n"
        "| 设计说明 | 决策；原因；约束；被否决方案；后续验证。 |\n"
        "| 会议整理 | 背景；结论；行动项；负责人；时间；待确认问题。 |\n\n"
        "## 人名和昵称\n\n"
        "- 公共人名、账号、昵称先查 `项目大脑/初始分工表.md`。\n"
        "- 当前 author 的私有别名和沟通偏好查 `沟通偏好.md`。\n"
        "- 会议 AI 转写里的错别字、谐音名、简称，应补到对应用户的私人 `沟通偏好.md` 或 `职责范围.md` 的昵称区域。\n\n"
        "## 维护规则\n\n"
        "- 新规则必须是当前 author 长期可复用的沟通经验。\n"
        "- 团队通用规则不写到根目录 `沟通规范.md`；应先在任务记录中沉淀，再由 leader 判断是否进入 `主规则.md`、`主流程.md`、`docs/规则/`、公共 skill 或对应 docs。\n"
        "- 本文件只写沟通方式，不写具体任务过程、bug 结论或代码实现细节。\n"
    )


def build_private_flow_text(author: str, assignment: dict[str, str] | None = None) -> str:
    required_flow_rows = [
        "| 1 | `私有规则.md` |",
        "| 2 | 当前任务 `process.md` |",
        "| 3 | 当前任务 `design.md` |",
        "| 4 | 当前任务 `任务范围.md` |",
        "| 5 | 当前任务 `任务指标.md` |",
    ]
    if should_load_nontech_safety_guardrail(author, assignment):
        required_flow_rows = [
            f"| 1 | `{NONTECH_SAFETY_GUARDRAIL_PATH}` |",
            "| 2 | `私有规则.md` |",
            "| 3 | 当前任务 `process.md` |",
            "| 4 | 当前任务 `design.md` |",
            "| 5 | 当前任务 `任务范围.md` |",
            "| 6 | 当前任务 `任务指标.md` |",
        ]
    required_flow_table = "\n".join(required_flow_rows)

    return (
        "# 私有流程\n\n"
        f"> author: `{author}`\n"
        "本文只说明当前 author 的私有加载流程。公共 `主流程.md` 读取本文后，按本文件加载当前 author 的私有规则和当前任务上下文；不同 author 的经验互不替代。\n\n"
        "私人 `index.md` 是当前 author 的私人文献索引，可能持续增长。它只在需要查找私人资料、经验、方法、交付规则或常用文档时按需查询，不作为冷启动或恢复任务的必读全文；新增后将超过 50 条或已经超过 50 条时，用 `project-index-maintenance` 自动分级到 `索引/{category}.md`。\n\n"
        "## 必读加载流程\n\n"
        "| 顺序 | 读取文档 |\n"
        "| --- | --- |\n"
        f"{required_flow_table}\n\n"
        "## 条件加载流程\n\n"
        "| 触发场景 | 读取文档或 skill |\n"
        "| --- | --- |\n"
        "| 需要当前 author 的常用资料、私人经验、方法、checklist 或交付规则入口 | 按需查询 `index.md` 的相关条目，再读取被索引文档；不要把私人 `index.md` 全文当作启动必读 |\n"
        "| 私人 `index.md` 新增入口后将超过 50 条，或已经超过 50 条 | `项目大脑/skills/project-index-maintenance/`；自动分级为私人二级索引，再写入具体入口 |\n"
        "| 涉及当前 author 的职责、修改区域或交付规则 | `职责范围.md` |\n"
        "| 涉及当前 author 的身份、人名、昵称或转写别名 | `用户画像.md`、`沟通偏好.md` |\n"
        "| 涉及当前 author 的工作方式、验收偏好或沟通方式 | `工作习惯.md`、`协作灵魂.md`、`沟通规范.md` |\n"
        "| 需要查找当前 author 的历史经验、方法、索引或 checklist | 按需查询 `index.md` 后读取匹配的 `经验/{主题}.md`；不要全量读取 `经验/` |\n"
        "| 搜索文档、配置、脚本、表格、美术资源、会议记录、设计思路或任意项目资料 | `项目大脑/skills/project-search-map/`；先读当前 author `资料地图/index.md`，再读公共 `项目大脑/资料地图/index.md` |\n"
        "| 搜索代码、调研相似功能、定位入口、评估复用或准备改代码 | 先读取当前 author `代码地图/index.md` 和 `代码地图/差异列表.md`，再读取公共 `项目大脑/代码地图/index.md`；按需读取命中的主题文档，并在首轮工具查询前产出查询规划卡 |\n"
        "| 带查询规划卡验证后找到稳定代码入口、相似实现或查找路径 | 更新当前 author `代码地图/{主题}.md` 和 `代码地图/index.md`；公共地图缺失或不一致时更新当前 author `代码地图/差异列表.md` |\n"
        "| 需求很大、边界不清、跨模块或评估复杂 | 按需查询 `index.md` 后读取匹配的 `经验/{需求梳理、需求拆解、计划方法}.md`；先拆阶段、定验收，再一阶段一阶段执行 |\n"
        "| 需要测试、验收、回归或日志分析 | 按需查询 `index.md` 后读取匹配的 `经验/{测试方法、验收方法、日志分析}.md` |\n"
        "| 需要搭建环境、安装依赖或启动服务 | 按需查询 `index.md` 后读取匹配的 `经验/{环境搭建、工具环境}.md` |\n"
        "| 需要写文档、整理会议或输出说明 | 按需查询 `index.md` 后读取匹配的 `经验/{写文档、文档方法}.md` |\n"
        "| 需要调试、查 bug、定位崩溃或分析异常 | 按需查询 `index.md` 后读取匹配的 `经验/{调试方法、问题定位}.md` |\n"
        "| 需要当前 author 私有 skill / MCP / hook | `skills/`、`mcps/`、`hooks/` |\n"
        "| 需要继续旧任务或 fork 旧任务 | `tasklist.md`、`项目大脑/skills/resume-project-task/`、`项目大脑/skills/fork-project-task/` |\n"
        "| 创建/恢复 task、绑定 task 后读四件套，或准备改仓库 / agent 委托 / bulk 任务 | ① `项目大脑/团队经验/INDEX.md` → 点读 1–3 篇 `entries/`，写 `process.md`「团队经验引用」；② `用户/{author}/经验/INDEX.md` → 点读 0–3 篇 `entries/`，写「个人经验引用」（无条目写「暂无」）；③ `公理/index.md`「任务开始路由」→ 点读 0–2 篇 `entries/`，写「团队公理引用」（无匹配写「暂无」） |\n"
        "| 用户要求学习个人习惯 | `项目大脑/skills/learn-user-habits/` |\n"
        "| 用户要求总结或沉淀经验 | `项目大脑/skills/personal-experience-curation/`；写入当前 author 私人 `经验/` 并更新 `index.md` |\n\n"
        "## 收尾前加载流程\n\n"
        "| 顺序 | 读取文档或目录 |\n"
        "| --- | --- |\n"
        "| 1 | 当前任务 `process.md`、`design.md` |\n"
        "| 2 | 当前任务 `任务范围.md`、`任务指标.md` |\n"
        "| 3 | 如果任务状态变化，更新或读取 `tasklist.md` |\n"
        "| 4 | 如果本轮新增或修改私人资料、经验或交付规则，按需更新 `index.md` 的相关条目 |\n"
        "| 5 | 如果本轮搜索过代码并形成可复用入口，按需更新当前 author `代码地图/` 和 `代码地图/差异列表.md` |\n"
        "| 6 | 如果本轮搜索过文档、配置、表格、脚本、美术资源、会议或设计思路并形成可复用入口，按需更新当前 author `资料地图/` |\n"
        "| 7 | 如果 `index.md`、代码地图或资料地图主题索引将超过或已经超过 50 条，运行 `project-index-maintenance` 或按主题自动分级 |\n"
        "| 8 | 本轮相关或新增的被索引文档 / `经验/{主题}.md` / `代码地图/{主题}.md` / `资料地图/{主题}.md` |\n"
    )


def build_private_rules_text(author: str) -> str:
    return (
        "# 私有规则\n\n"
        f"> author: `{author}`\n"
        "> 用途: 当前 author 的私人工作规则。公共 `主规则.md` 先加载，本文只补充当前 author 的私有规则。\n\n"
        "## 优先级\n\n"
        "- 本文件不得覆盖公共 `主规则.md`、代码规范、模块约束或用户当次明确要求。\n"
        "- 本文件只约束当前 author 的用户区、任务区、私人索引、私人经验和私人扩展目录。\n"
        "- 本文件和 `职责范围.md` 冲突时，先按 `职责范围.md` 控制修改边界，再在当前任务 `process.md` 记录需要修正的规则。\n\n"
        "## 私人区规则\n\n"
        "- 私人 `index.md` 只放当前 author 私人入口和用户主动加入的常用资料入口。\n"
        "- 私人 `index.md` 即将超过或已经超过 50 条入口时，用 `project-index-maintenance` 自动分级；一级索引只保留分类入口，具体文档写入 `索引/{category}.md`。\n"
        "- `私有流程.md` 只写当前 author 的加载流程，不写规则正文。\n"
        "- `私有规则.md` 只写当前 author 的长期私有规则，不写单个任务流水。\n"
        "- 每个 author 的经验不同；总结触发后，可复用经验默认写当前 author 私人 `经验/`，并更新当前 author 私人 `index.md`，供后续按需查阅。\n"
        "- 私有加载不把私人 `index.md` 作为必读全文；需要私人资料、经验、方法、checklist 或交付规则时，按当前目标和用户指令查询 `index.md` 的相关条目，再读取匹配文档。\n"
        "- 私人经验可以是历史教训、方法文档、索引、按需加载指南、checklist 或模板；不要全量加载 `经验/` 目录。\n"
        "- 私人经验先评判再沉淀；缺少来源、前因、结论、证据或复用场景的流水账不写入经验库。\n"
        "- 私人经验索引摘要和总结正文都必须写清前因后果；否则后续读取时无法判断价值。\n"
        "- 私人经验按积累触发整理；入口为 `项目大脑/skills/personal-experience-curation/`，不做冷启动到期检查。\n"
        "- 私人 `代码地图/` 是当前 author 的代码查询规划层；搜索代码、调研相似实现、定位入口或评估复用前先查私人地图，再查公共地图，并产出查询规划卡：候选入口/目录、关键词/别名、反向追踪路径、排除项和待验证问题。地图都未命中、过期或需要验证调用路径时，也先写最小假设表，再带锚点用 CodeGraph 或限定目录 `rg` 验证。\n"
        "- 带查询规划卡完成源码验证后，只把稳定结果回写当前 author 私人代码地图；不因日常任务直接写公共代码地图。\n"
        "- 私人 `代码地图/差异列表.md` 记录私人地图与公共地图的缺失、过期和冲突；leader 公共化时据此核实和抽取。\n"
        "- 私人代码地图只记录路径、函数名、类名、变量名、协议名、配置名、数据表名和查找经验；不记录行号，不一条搜索一个文件。\n"
        "- 发现私人代码地图旧记录与最新代码不一致时，更新原主题文档的最后更新时间、验证版本和入口说明。\n"
        "- 私人 `资料地图/` 是当前 author 的非代码资料定位知识库；搜索文档、配置、表格、脚本、美术资源、会议记录或设计思路前先查私人资料地图，再查公共资料地图。\n"
        "- 普通搜索后只把稳定非代码入口回写当前 author 私人资料地图；不因日常任务直接写公共资料地图。\n"
        "- 私人资料地图只记录目录、文件名、表格名、配置 key、资源 key、文档标题、会议标题、云文档链接和查找经验；不复制正文，不记录行号。\n"
        "- 当前任务过程写入当前任务 `process.md`；稳定设计决策写入当前任务 `design.md`。\n"
        "- 当前任务工作项和证据写入 `任务范围.md`；阶段指标写入 `任务指标.md`。\n"
        "- 当前 author 的经验先按主题聚类写入 `经验/`，稳定后再由 leader 判断是否公共化。\n"
        "- 遇到特别大的需求、边界不清、跨模块或评估复杂的目标时，不要直接开写；先按私人经验里的需求梳理/需求拆解方法确认范围、查相似实现、拆阶段、定义每阶段验收，再一阶段一阶段推进。\n\n"
        "## 修改边界\n\n"
        "- 准备修改仓库文件前先读取 `职责范围.md`。\n"
        "- 不直接修改其他 author 的 `项目大脑/用户/{other-author}/` 私有用户区。\n"
        "- 新建 skill、MCP、hook 或辅助落盘默认放当前 author 私有区。\n"
        "- 公共化建议先写入当前任务记录，经过验证后由 leader 收口到公共区。\n\n"
        "## 维护规则\n\n"
        "- 当前 author 可以维护自己的 `私有流程.md`、`私有规则.md`、`index.md`、`职责范围.md` 和软上下文文件。\n"
        "- 修改本文件时写清原因；如果是任务中形成的新规则，同时在当前任务 `design.md` 或 `process.md` 记录来源。\n"
        "- 不把一次性任务偏好、临时 workaround 或未验证结论写成长期私有规则。\n"
    )


def build_index_text(author: str) -> str:
    return (
        "# 用户目录索引\n\n"
        f"> author: `{author}`\n"
        "> 用途: 当前 author 私人用户区的快速查阅入口。\n\n"
        "本文只放链接和一句话用途，不复制被引用文档正文，不记录任务过程。"
        "每个用户区只保留这一份一级 `index.md`；新增后将超过 50 条或已经超过 50 条时，用 `project-index-maintenance` 自动分级到 `索引/{category}.md`。\n\n"
        "## 私人区入口\n\n"
        "| 文档 | 用途 |\n"
        "| --- | --- |\n"
        "| `私有流程.md` | 公共主流程之后的当前 author 私有加载流程。 |\n"
        "| `私有规则.md` | 当前 author 的长期私有规则；不覆盖公共主规则。 |\n"
        "| `职责范围.md` | 当前 author 的职责、修改边界和协作边界。 |\n"
        "| `用户画像.md` | 当前 author 的长期稳定背景、昵称和协作上下文。 |\n"
        "| `工作习惯.md` | 当前 author 的稳定工作方式、验收偏好和协作节奏。 |\n"
        "| `协作灵魂.md` | 当前 author 与 agent 协作时的软基调。 |\n"
        "| `沟通规范.md` | 当前 author 与 agent 协作时的稳定沟通规范。 |\n"
        "| `沟通偏好.md` | 当前 author 的沟通偏好、称呼和会议转写别名。 |\n"
        "| `经验/` | 当前 author 从任务中沉淀的新知识、经验、规则、教训、方法、索引和 checklist，按主题聚类文档保存。 |\n"
        "| `经验/INDEX.md` | 经验认知分类索引；整理入口见公共 `personal-experience-curation`。 |\n"
        "| `代码地图/` | 当前 author 私人代码查询规划层；搜索代码前先查私人再查公共，产出查询规划卡后带锚点验证，稳定经验按主题更新，并用 `差异列表.md` 记录与公共地图的差异。 |\n"
        "| `资料地图/` | 当前 author 私人非代码资料定位知识库；搜索文档、配置、脚本、表格、美术资源、会议记录或设计思路前先查。 |\n"
        "| `tasklist.md` | 当前 author 的任务摘要和任务目录索引。 |\n"
        "| `tasks/` | 当前 author 的任务过程、设计决策和交接材料。 |\n\n"
        "## 用户主动添加的常用入口\n\n"
        "本节只放当前 author 明确要求加入、或任务中确认会反复使用的快速入口。"
        "不要默认复制公共 `index.md`、启动文件、主流程、主规则或公共 skill 清单。\n\n"
        "| 文档 | 用途 |\n"
        "| --- | --- |\n"
    )


def build_experience_curation_state_text(author: str) -> str:
    return (
        "# 私人经验整理状态\n\n"
        f"> author: `{author}`\n"
        "> 用途: 记录当前 author 私人经验周期整理的节流状态和整理锁。\n"
        "> 规则: 私人整理每天最多 2 次，间隔至少 8 小时；开始后台整理前先写入 running 锁。\n\n"
        "## 状态表\n\n"
        "| scope | status | last_curated_at | started_at | lock_owner | lock_expires_at | runs_today_date | runs_today | min_interval_hours | max_runs_per_day | last_actor | 说明 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        f"| private-experience | idle |  |  |  |  |  | 0 | 8 | 2 | {author} | 当前 author 私人经验整理。 |\n\n"
        "## 使用规则\n\n"
        "- 本文件只由 `private-experience-curation` 检查、抢锁和标记。\n"
        "- 主流程加载时可用 `--acquire-start` 抢锁；抢到锁后再启动后台整理子 agent。\n"
        "- 只有私人整理子 agent 成功写入当前 author 私人经验后，才使用 `--mark private-experience` 更新 `last_curated_at` 并释放锁。\n"
        "- 整理失败时使用 `--release-failed` 释放锁并保留失败状态，方便下次重试和排查。\n"
        "- 多 worktree 合并冲突时，保留较新的 `last_curated_at`；同一天 `runs_today` 取较大值；仍在有效期内的 running 锁不得被覆盖为 idle。\n"
        "- 不把任务过程写入本文件；过程写当前任务 `process.md`，经验写 `经验/{主题}.md`。\n"
    )


def build_leader_curation_state_text() -> str:
    return (
        "# Leader 公共抽取状态\n\n"
        f"> author: `{LEADER_AUTHOR}`\n"
        "> 用途: 记录 leader 公共经验抽取的节流状态和整理锁。\n"
        "> 规则: leader 公共抽取每天最多 1 次，间隔至少 24 小时；开始后台整理前先写入 running 锁。\n\n"
        "## 状态表\n\n"
        "| scope | status | last_curated_at | started_at | lock_owner | lock_expires_at | runs_today_date | runs_today | min_interval_hours | max_runs_per_day | last_actor | 说明 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        f"| leader-public-curation | idle |  |  |  |  |  | 0 | 24 | 1 | {LEADER_AUTHOR} | 仅 tech-lead 抽取公共规则（docs/规则/）、代码地图和资料地图。 |\n\n"
        "## 使用规则\n\n"
        "- 本文件只由 `leader-public-curation` 检查、抢锁和标记。\n"
        "- 主流程加载且 `author=tech-lead` 时可用 `--acquire-start` 抢锁；抢到锁后再启动后台公共抽取子 agent。\n"
        "- 只有 leader 公共抽取子 agent 成功写入公共区后，才使用 `--mark leader-public-curation` 更新 `last_curated_at` 并释放锁。\n"
        "- 整理失败时使用 `--release-failed` 释放锁并保留失败状态，方便下次重试和排查。\n"
        "- 多 worktree 合并冲突时，保留较新的 `last_curated_at`；同一天 `runs_today` 取较大值；仍在有效期内的 running 锁不得被覆盖为 idle。\n"
        "- 不把任务过程写入本文件；公共结论写 `docs/规则/`、`代码地图/`、`资料地图/`。\n"
    )


def build_code_map_index_text(author: str) -> str:
    return (
        "# 代码地图索引\n\n"
        f"> author: `{author}`\n"
        "> 用途: 当前 author 私人代码查询规划层。搜索代码前先看这里，用地图产出查询规划卡，再把可复用定位结果反哺回来。\n\n"
        "## 使用规则\n\n"
        "- 搜索代码、调研相似功能、定位入口、评估复用或排查调用链前，先查本索引和 `差异列表.md`，再查公共 `项目大脑/代码地图/index.md`。\n"
        "- 私人地图命中时优先使用私人结论；公共地图只作对照和兜底。\n"
        "- 读完地图后先产出查询规划卡：候选入口/目录、关键词/别名、反向追踪路径、排除项、待验证问题和首轮工具锚点。\n"
        "- 没有命中、明显过期或需要验证调用路径时，也先写最小假设表，再带地图锚点用 CodeGraph / 限定目录 `rg` 验证；找到结果后，把稳定入口和查找经验追加到当前 author 私人主题文档。\n"
        "- 私人地图和公共地图缺失或不一致时，更新 `差异列表.md`，方便 leader 周期抽取公共地图。\n"
        "- 记录文件路径、函数名、类名、变量名、协议名、配置名、数据表名和查找经验；不记录行号。\n"
        "- 每个主题文档写最后更新时间、最后验证版本、相关任务和相关入口；发现旧记录失效时直接更新原主题。\n"
        "- 按稳定主题聚类；同类内容追加到已有文档，不一条搜索一个文件。\n\n"
        "## 主题索引\n\n"
        "| 主题 | 用途 |\n"
        "| --- | --- |\n"
        "| `差异列表.md` | 当前 author 私有代码地图与公共代码地图的缺失、过期和冲突记录。 |\n"
        "| 待补充 | 当前私人代码地图尚未沉淀主题；先写最小假设表，再带锚点验证，搜索后按主题补充。 |\n"
    )


def build_code_map_diff_text(author: str) -> str:
    return (
        "# 代码地图差异列表\n\n"
        f"> author: `{author}`\n"
        "> 用途: 记录当前 author 私有代码地图与公共代码地图之间的差异，供 leader 公共化抽取和核实。\n"
        "> 规则: 搜索和更新只写当前 author 私有差异；公共地图更新并核实覆盖后，再移除对应条目。\n\n"
        "## 使用规则\n\n"
        "- 搜索代码前先看当前 author 私人代码地图，再看公共代码地图。\n"
        "- 私人地图和公共地图不一致时，优先按私人地图继续搜索，但必须记录差异和证据。\n"
        "- 只记录路径、函数名、类名、变量名、协议名、配置名、数据表名和查找经验；不记录行号。\n"
        "- leader 更新公共地图后，核实公共记录已经覆盖私有差异，再移除对应条目；跨 author 私有区清理需要该 author 会话执行或用户明确授权。\n\n"
        "## 差异表\n\n"
        "| id | 状态 | 差异类型 | 私有主题 | 公共主题 | 私有结论 | 公共结论 | 证据 | 更新时间 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )


def build_resource_map_index_text(author: str) -> str:
    return (
        "# 资料地图索引\n\n"
        f"> author: `{author}`\n"
        "> 用途: 当前 author 私人非代码资料定位知识库。搜索文档、配置、脚本、表格、美术资源、会议记录或设计思路前先看这里；普通搜索后把可复用定位结果反哺回来。\n\n"
        "## 使用规则\n\n"
        "- 搜索文档、配置、脚本、表格、美术资源、会议记录、设计思路或外部资料前，先查本索引，再查公共 `项目大脑/资料地图/index.md`。\n"
        "- 没有命中时走公共/私人 `index.md`、`rg --files` / `rg`。\n"
        "- 找到稳定入口后，把目录、文件名、表格名、配置 key、资源 key、文档标题、会议标题、云文档链接和查找经验追加到当前 author 私人主题文档。\n"
        "- 资料地图只记录“在哪里找”和“如何查”，不复制正文，不记录行号。\n"
        "- 按稳定主题聚类；同类内容追加到已有文档，不一条搜索一个文件。\n\n"
        "## 主题索引\n\n"
        "| 主题 | 用途 |\n"
        "| --- | --- |\n"
        "| 待补充 | 当前私人资料地图尚未沉淀主题；先普通搜索，搜索后按主题补充。 |\n"
    )


def write_user_files(repo_root: Path, author: str, dry_run: bool) -> None:
    root = user_root(repo_root, author)
    assignment = read_initial_assignment(repo_root, author)
    relative_user_dir = normalize_relative_path(repo_root, root, trailing_slash=True)
    private_flow = root / PRIVATE_FLOW_FILENAME
    private_rules = root / PRIVATE_RULES_FILENAME
    index = root / INDEX_FILENAME
    tasklist = root / "tasklist.md"
    responsibility = root / RESPONSIBILITY_FILENAME
    user_profile = root / USER_PROFILE_FILENAME
    work_habits = root / WORK_HABITS_FILENAME
    collaboration_soul = root / COLLABORATION_SOUL_FILENAME
    communication_norms = root / COMMUNICATION_NORMS_FILENAME
    communication_preferences = root / COMMUNICATION_PREFERENCES_FILENAME
    experience_dir = root / EXPERIENCE_DIRNAME
    private_experience_curation_state = experience_dir / PRIVATE_EXPERIENCE_CURATION_STATE_FILENAME
    leader_curation_state = experience_dir / LEADER_CURATION_STATE_FILENAME
    code_map_dir = root / CODE_MAP_DIRNAME
    code_map_index = code_map_dir / INDEX_FILENAME
    code_map_diff = code_map_dir / CODE_MAP_DIFF_FILENAME
    resource_map_dir = root / RESOURCE_MAP_DIRNAME
    resource_map_index = resource_map_dir / INDEX_FILENAME
    tasks_dir = root / TASKS_DIRNAME
    private_extension_dirs = [root / dirname for dirname in PRIVATE_EXTENSION_DIRNAMES]
    relative_private_flow = normalize_relative_path(repo_root, private_flow)
    relative_private_rules = normalize_relative_path(repo_root, private_rules)
    relative_index = normalize_relative_path(repo_root, index)
    relative_tasklist = normalize_relative_path(repo_root, tasklist)
    relative_responsibility = normalize_relative_path(repo_root, responsibility)
    relative_user_profile = normalize_relative_path(repo_root, user_profile)
    relative_work_habits = normalize_relative_path(repo_root, work_habits)
    relative_collaboration_soul = normalize_relative_path(repo_root, collaboration_soul)
    relative_communication_norms = normalize_relative_path(repo_root, communication_norms)
    relative_communication_preferences = normalize_relative_path(repo_root, communication_preferences)
    relative_experience_dir = normalize_relative_path(repo_root, experience_dir, trailing_slash=True)
    relative_private_experience_curation_state = normalize_relative_path(repo_root, private_experience_curation_state)
    relative_leader_curation_state = normalize_relative_path(repo_root, leader_curation_state)
    relative_code_map_dir = normalize_relative_path(repo_root, code_map_dir, trailing_slash=True)
    relative_code_map_index = normalize_relative_path(repo_root, code_map_index)
    relative_code_map_diff = normalize_relative_path(repo_root, code_map_diff)
    relative_resource_map_dir = normalize_relative_path(repo_root, resource_map_dir, trailing_slash=True)
    relative_resource_map_index = normalize_relative_path(repo_root, resource_map_index)
    relative_tasks_dir = normalize_relative_path(repo_root, tasks_dir, trailing_slash=True)
    relative_private_extension_dirs = [
        normalize_relative_path(repo_root, path, trailing_slash=True)
        for path in private_extension_dirs
    ]

    if dry_run:
        print(f"Would ensure user directory: {relative_user_dir}")
        print(f"Would ensure private flow: {relative_private_flow}")
        print(f"Would ensure private rules: {relative_private_rules}")
        print(f"Would ensure user index: {relative_index}")
        print(f"Would ensure responsibility file: {relative_responsibility}")
        print(f"Would ensure user profile: {relative_user_profile}")
        print(f"Would ensure work habits: {relative_work_habits}")
        print(f"Would ensure collaboration soul: {relative_collaboration_soul}")
        print(f"Would ensure communication norms: {relative_communication_norms}")
        print(f"Would ensure communication preferences: {relative_communication_preferences}")
        print(f"Would ensure experience directory: {relative_experience_dir}")
        print(f"Would ensure private experience curation state: {relative_private_experience_curation_state}")
        if author == LEADER_AUTHOR:
            print(f"Would ensure leader public curation state: {relative_leader_curation_state}")
        print(f"Would ensure code map directory: {relative_code_map_dir}")
        print(f"Would ensure code map index: {relative_code_map_index}")
        print(f"Would ensure code map diff list: {relative_code_map_diff}")
        print(f"Would ensure resource map directory: {relative_resource_map_dir}")
        print(f"Would ensure resource map index: {relative_resource_map_index}")
        print(f"Would ensure task index: {relative_tasklist}")
        print(f"Would ensure tasks directory: {relative_tasks_dir}")
        for relative_path in relative_private_extension_dirs:
            print(f"Would ensure private extension directory: {relative_path}")
        return

    root.mkdir(parents=True, exist_ok=True)
    experience_dir.mkdir(parents=True, exist_ok=True)
    code_map_dir.mkdir(parents=True, exist_ok=True)
    resource_map_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for extension_dir in private_extension_dirs:
        extension_dir.mkdir(parents=True, exist_ok=True)
    write_gitkeep_if_empty(experience_dir)
    if not code_map_index.exists():
        code_map_index.write_text(build_code_map_index_text(author), encoding="utf-8")
    if not code_map_diff.exists():
        code_map_diff.write_text(build_code_map_diff_text(author), encoding="utf-8")
    if not resource_map_index.exists():
        resource_map_index.write_text(build_resource_map_index_text(author), encoding="utf-8")
    write_gitkeep_if_empty(tasks_dir)
    for extension_dir in private_extension_dirs:
        write_gitkeep_if_empty(extension_dir)
    if not private_flow.exists():
        private_flow.write_text(build_private_flow_text(author, assignment), encoding="utf-8")
    if not private_rules.exists():
        private_rules.write_text(build_private_rules_text(author), encoding="utf-8")
    if not index.exists():
        index.write_text(build_index_text(author), encoding="utf-8")
    if not responsibility.exists():
        responsibility.write_text(
            build_responsibility_text(author, assignment),
            encoding="utf-8",
        )
    if not user_profile.exists():
        user_profile.write_text(build_user_profile_text(author, assignment), encoding="utf-8")
    if not work_habits.exists():
        work_habits.write_text(build_work_habits_text(author, assignment), encoding="utf-8")
    if not collaboration_soul.exists():
        collaboration_soul.write_text(build_collaboration_soul_text(author), encoding="utf-8")
    if not communication_norms.exists():
        communication_norms.write_text(build_communication_norms_text(author), encoding="utf-8")
    if not communication_preferences.exists():
        communication_preferences.write_text(
            build_communication_preferences_text(author, assignment),
            encoding="utf-8",
        )
    if not tasklist.exists():
        tasklist.write_text(
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
    print(f"User directory ready: {relative_user_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a base project-info user workspace.")
    parser.add_argument("--repo-root", help="Repository root. Defaults to auto-detect.")
    parser.add_argument("--author", help="Current remote account / author folder name.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned paths without writing files.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    author = validate_author(args.author or load_workstate_author(repo_root) or "")
    canonical_author = resolve_canonical_author(author)
    if canonical_author != author:
        print(f"Author alias resolved: {author} -> {canonical_author}")
    write_user_files(repo_root, canonical_author, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
