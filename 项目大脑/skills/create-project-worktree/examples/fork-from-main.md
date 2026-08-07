# 场景：从旧任务 fork 到新 worktree

当前在 `main` 或基础分支，用户希望基于旧任务上下文启动一个新的任务 worktree。

```powershell
$S = "项目大脑/skills/create-project-worktree/scripts"
python "$S/create_project_worktree.py" `
  --author "{author}" `
  --task-slug project-info-followup `
  --task-name "项目大脑后续实验" `
  --request "基于之前的项目大脑任务继续做一个新实验" `
  --goal "继承旧任务上下文，但用新 worktree 和新任务目录记录新目标" `
  --fork-from-task-folder "项目大脑/用户/{author}/tasks/YYYY-MM-DD-example/"
```

完成后进入脚本输出的新 worktree。新任务目录是源任务目录内容的副本；`process.md` / `design.md` 顶部会改为新任务目标，源任务记录保留在同文件下方快照区。`任务范围.md` 和 `任务指标.md` 使用新任务内容重新生成，不继承旧任务状态。
