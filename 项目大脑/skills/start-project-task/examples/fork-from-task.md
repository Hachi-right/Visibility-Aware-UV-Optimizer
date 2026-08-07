# 场景：从旧任务 fork 新任务

用户希望基于旧任务的上下文快速启动一个新任务，但新任务要有新的任务名、目标和进度记录。

```powershell
$S = "项目大脑/skills/start-project-task/scripts"
python "$S/start_task.py" `
  --author "{author}" `
  --task-slug project-info-followup `
  --task-name "项目大脑后续实验" `
  --request "基于之前的项目大脑任务继续做一个新实验" `
  --goal "继承旧任务上下文，但用新任务目录记录新目标和新进度" `
  --fork-from-task-folder "项目大脑/用户/{author}/tasks/YYYY-MM-DD-example/" `
  --replace-current
```

完成后，新任务目录是源任务目录内容的副本；`process.md` / `design.md` 顶部会改为新任务目标，源任务记录保留在同文件下方快照区。`任务范围.md` 和 `任务指标.md` 使用新任务内容重新生成，不继承旧任务状态。
