from __future__ import annotations

"""Canonical author resolution for project-brain user directories.

Keep in sync with `项目大脑/账号别名.md`.
"""

# alias author -> canonical user directory author
AUTHOR_ALIASES: dict[str, str] = {
    "lilin": "lilin1",
    "caoqin": "caoqing2",
    "caoqin5": "caoqing2",
    "xutong": "xutong1",
}


def resolve_canonical_author(author: str) -> str:
    return AUTHOR_ALIASES.get(author.strip(), author.strip())
