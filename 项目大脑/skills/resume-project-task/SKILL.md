---
name: resume-project-task
description: 需要继续当前 author 私人任务索引中的旧任务，并把 workstate.json.task_folder 重新指向旧任务目录时使用。
---

# resume-project-task

本 skill 用于继续旧任务，不创建新任务目录，不追加 `tasklist.md`。

## 使用时机

- 用户明确说“继续之前的某个任务”。
- 启动时发现当前请求和 `项目大脑/用户/{author}/tasklist.md` 中已有任务高度相似，且用户确认要继续旧任务。

## 流程

1. 读取 `项目大脑/用户/{author}/tasklist.md`。
2. 让用户确认目标任务目录；不能凭相似度自动切换。
3. 运行：

```powershell
python 项目大脑/skills/generate-workstate/scripts/generate_workstate.py `
  --author "{author}" `
  --task-folder "项目大脑/用户/{author}/tasks/{task-id}/" `
  --source task-resumed
```

4. 校验目标任务目录存在 `process.md` 和 `design.md`。
5. 读取目标任务的 `process.md` 和 `design.md`，继续执行。
6. 若 `process.md` 无「团队经验引用」/「个人经验引用」/「团队公理引用」或任务目标已变化：按 `主流程.md` §3 重跑 INDEX 路由，更新引用段与 `任务范围.md` 中 `team-exp-routing`、`personal-exp-routing`、`team-axiom-routing`（若无该行则追加）。

## 约束

- 只允许指向当前 author 用户区下的任务目录。
- 不修改旧任务名，不复制旧任务目录。
- 不向 `tasklist.md` 追加新行。
