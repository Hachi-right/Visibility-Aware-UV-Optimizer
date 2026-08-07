---
name: project-startup-check
description: 需要执行项目大脑冷启动检查、校验分支/workstate/用户区/当前 author 私有扩展目录/当前任务，或用 Python 稳定补齐启动所需目录时使用。
---

# project-startup-check

本 skill 是项目大脑的冷启动编排入口。它用 Python 串起已有小 skill 的脚本，减少 agent 手工逐项核查。

## 脚本表

| 脚本 | 用途 |
| --- | --- |
| `scripts/startup_check.py` | 检查分支、`workstate.json`、用户基础结构、`经验/`、`代码地图/` 与 `差异列表.md`、`资料地图/`、当前 author 私有扩展目录和当前任务指针；必要时补齐用户区和私有目录；输出启动阶段、用户识别、任务识别和 `next_action` |

## 使用方式

```powershell
python 项目大脑/skills/project-startup-check/scripts/startup_check.py --repo-root .
```

常见参数：

| 参数 | 用途 |
| --- | --- |
| `--author "{author}"` | 没有 `workstate.json` 时，用已确认 author 初始化空任务指针。 |
| `--intends-file-change` | 本轮请求需要改仓库文件时启用；脚本显示主工作区/独立任务副本与绝对路径，并在 main/master 时提示按 worktree 闸门判断。 |
| `--open-workspace` | 显式在资源管理器打开当前工作目录；不会在普通启动时自动弹窗。 |
| `--check-only` | 只检查不创建目录。 |
| `--json` | 输出机器可读结果。 |

脚本通过后，再按它输出的 `next_action` 继续：任务为空时走 `start-project-task` / `resume-project-task` / `fork-project-task`；任务有效时先进入公共 `主流程.md`，再由当前 author `私有流程.md` 加载私有规则和当前任务上下文。

启动输出要让用户能核对当前身份和任务：看到 `识别到当前用户`、`用户描述`、`找到当前任务目录`、`任务描述` 后，如果用户表示不正确，先停止并修正 `workstate.json` 或任务指针。
