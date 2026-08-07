---
name: start-project-task
description: 需要开启新的项目大脑任务、创建任务目录和 process.md/design.md/任务范围.md/任务指标.md、更新 workstate.json.task_folder，或判断 main/master 是否需要 worktree 隔离时使用。
---

# start-project-task

## 参考文档表

| 主题 | 文件 | 内容 |
|------|------|------|
| 任务启动规则 | `references/start-task.md` | 参数、产物、已有任务处理 |
| worktree 闸门 | `references/worktree-gate.md` | main/master 和分支内新任务规则 |

## 示例表

| 场景 | 文件 | 内容 |
|------|------|------|
| 分支内开启任务 | `examples/start-on-branch.md` | 创建任务目录并更新 workstate |
| 已有任务时切换 | `examples/replace-current.md` | 用户确认后替换当前任务指针 |
| 从旧任务 fork | `examples/fork-from-task.md` | 复制旧任务目录内容并创建新任务 |

## 脚本表

| 脚本 | 用途 |
|------|------|
| `scripts/start_task.py` | 创建任务目录，写入 `process.md`、`design.md`、`任务范围.md`、`任务指标.md`，更新根目录 `workstate.json`，并追加用户 `tasklist.md` |
