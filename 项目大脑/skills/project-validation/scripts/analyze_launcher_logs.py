from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_LOG_DIR = REPO_ROOT / "runtime" / "logs"

BLOCKING_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"Traceback",
        r"\bERROR\b",
        r"\bCRITICAL\b",
        r"Unhandled",
        r"TypeError",
        r"ReferenceError",
        r"AssertionError",
        r"Address already in use",
        r"EADDRINUSE",
        r"workflow_error",
        r"工作流失败",
        r"思考失败",
        r"parse_cerebellum_decision",
        r"takes\s+\d+\s+positional",
        r"invalid .*response",
        r"missing content",
        r"must be string",
    ]
]
ACTION_FAILED_PATTERN = re.compile(r"\[server_mcp\.action_failed\]|OUT_OF_RANGE|ITEM_NOT_FOUND")
KNOWN_WARNING_PATTERNS = [
    re.compile(r"workflow\.optional_context.*profile_context unavailable", re.IGNORECASE),
    re.compile(r"favicon\.ico.*404", re.IGNORECASE),
]


@dataclass(frozen=True)
class LogMatch:
    path: str
    line: int
    text: str


def read_lines(path: Path, tail_lines: int) -> tuple[list[str], int]:
    line_list = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if tail_lines <= 0 or len(line_list) <= tail_lines:
        return line_list, 1
    return line_list[-tail_lines:], len(line_list) - tail_lines + 1


def is_known_warning(line_text: str) -> bool:
    return any(pattern.search(line_text) for pattern in KNOWN_WARNING_PATTERNS)


def scan_log_file(path: Path, tail_lines: int) -> tuple[list[LogMatch], list[LogMatch], list[LogMatch]]:
    blocking_match_list: list[LogMatch] = []
    action_failed_match_list: list[LogMatch] = []
    known_warning_match_list: list[LogMatch] = []
    line_list, first_line_number = read_lines(path, tail_lines)
    for offset, line_text in enumerate(line_list):
        line_number = first_line_number + offset
        match = LogMatch(path=path.as_posix(), line=line_number, text=line_text)
        if is_known_warning(line_text):
            known_warning_match_list.append(match)
            continue
        if ACTION_FAILED_PATTERN.search(line_text):
            action_failed_match_list.append(match)
            continue
        if any(pattern.search(line_text) for pattern in BLOCKING_PATTERNS):
            blocking_match_list.append(match)
    return blocking_match_list, action_failed_match_list, known_warning_match_list


def scan_log_dirs(log_dir_list: list[Path], tail_lines: int) -> dict[str, object]:
    file_list: list[Path] = []
    for log_dir in log_dir_list:
        if log_dir.exists():
            file_list.extend(sorted(log_dir.glob("*.log")))

    blocking_match_list: list[LogMatch] = []
    action_failed_match_list: list[LogMatch] = []
    known_warning_match_list: list[LogMatch] = []
    for path in file_list:
        blocking, action_failed, known_warning = scan_log_file(path, tail_lines)
        blocking_match_list.extend(blocking)
        action_failed_match_list.extend(action_failed)
        known_warning_match_list.extend(known_warning)

    return {
        "files_scanned": [path.as_posix() for path in file_list],
        "blocking_count": len(blocking_match_list),
        "action_failed_count": len(action_failed_match_list),
        "known_warning_count": len(known_warning_match_list),
        "blocking": [match.__dict__ for match in blocking_match_list[:50]],
        "action_failed": [match.__dict__ for match in action_failed_match_list[:50]],
        "known_warning": [match.__dict__ for match in known_warning_match_list[:50]],
    }


def run_self_test() -> int:
    blocking_line_list = [
        "Traceback (most recent call last):",
        "ERROR: workflow_error parse_cerebellum_decision failed",
    ]
    action_failed_line_list = [
        "[server_mcp.action_failed] tool=pick_up error=OUT_OF_RANGE message=距离太远",
        "[server_mcp.action_failed] tool=use_item error=ITEM_NOT_FOUND message=没有物品",
    ]
    known_warning_line_list = [
        "[workflow.optional_context] trace=t npc=n context=profile_context profile_context unavailable (OperationalError)",
        'INFO: 127.0.0.1 "GET /favicon.ico HTTP/1.1" 404 Not Found',
    ]
    assert all(any(pattern.search(line_text) for pattern in BLOCKING_PATTERNS) for line_text in blocking_line_list)
    assert all(ACTION_FAILED_PATTERN.search(line_text) for line_text in action_failed_line_list)
    assert all(is_known_warning(line_text) for line_text in known_warning_line_list)
    print("self-test ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze launcher CLI logs for validation errors.")
    parser.add_argument(
        "--log-dir",
        action="append",
        default=[],
        help="Log directory to scan. Defaults to runtime/logs.",
    )
    parser.add_argument("--tail-lines", type=int, default=2000, help="Lines to scan from each log. Use 0 for all.")
    parser.add_argument("--fail-on-action-failed", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    log_dir_list = [Path(value) for value in args.log_dir] if args.log_dir else [DEFAULT_LOG_DIR]
    result = scan_log_dirs(log_dir_list, args.tail_lines)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if int(result["blocking_count"]) > 0:
        return 1
    if args.fail_on_action_failed and int(result["action_failed_count"]) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
