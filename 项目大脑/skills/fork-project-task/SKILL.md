---
name: fork-project-task
description: 需要从当前 author 的旧任务派生一个新任务，复制旧任务目录内容到新任务目录但使用新任务名、新目标和新任务状态时使用。
---

# fork-project-task

本 skill 用于从旧任务派生新任务。它不是继续旧任务；新任务有自己的任务目录和 `tasklist.md` 条目。源任务内容会复制到新任务目录根部，启动流程仍按普通任务读取新任务 `process.md` 和 `design.md`。

## 使用时机

- 用户想“基于之前那个任务继续做一个新方向”。
- 当前请求需要继承旧任务上下文，但不应污染旧任务进度。

## 流程

1. 读取 `项目大脑/用户/{author}/tasklist.md`。
2. 让用户确认源任务目录、新任务名、新任务 slug、新目标。
3. 如果当前在 `main` 或 `master`，使用 `create-project-worktree`：

```powershell
python 项目大脑/skills/create-project-worktree/scripts/create_project_worktree.py `
  --author "{author}" `
  --task-slug "{new-task-slug}" `
  --task-name "{新任务名}" `
  --request "{用户原始需求}" `
  --goal "{新任务目标}" `
  --fork-from-task-folder "项目大脑/用户/{author}/tasks/{source-task-id}/"
```

4. 如果当前已经在任务分支，使用 `start-project-task`：

```powershell
python 项目大脑/skills/start-project-task/scripts/start_task.py `
  --author "{author}" `
  --task-slug "{new-task-slug}" `
  --task-name "{新任务名}" `
  --request "{用户原始需求}" `
  --goal "{新任务目标}" `
  --fork-from-task-folder "项目大脑/用户/{author}/tasks/{source-task-id}/" `
  --replace-current
```

5. 新任务创建后，按普通启动流程读取新任务 `process.md` 和 `design.md`；这两个文件顶部是新任务目标，下方保留源任务快照。

## 产物

| 产物 | 作用 |
| --- | --- |
| 新任务目录 | 复制源任务目录内容后形成的新任务工作区 |
| 新任务 `process.md` | 顶部记录新任务目标和后续过程，下方保留源任务 `process.md` 快照 |
| 新任务 `design.md` | 顶部记录新任务新增决策，下方保留源任务 `design.md` 快照 |
| 用户区 `tasklist.md` | 追加新任务索引 |

## 约束

- 源任务目录必须属于当前 author，且包含 `process.md` 和 `design.md`。
- fork 时复制源任务目录内容到新任务目录根部；跳过本机私有目录和历史遗留派生目录。
- 新任务顶部目标必须覆盖旧任务目标，避免 agent 误把旧目标当成当前目标。
- 不回写旧任务，不把新任务状态混入旧任务。
