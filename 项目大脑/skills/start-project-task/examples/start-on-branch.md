# 场景：分支内开启任务

当前已经在任务分支，用户明确要开始一个新任务。

```powershell
$S = "项目大脑/skills/start-project-task/scripts"
python "$S/start_task.py" `
  --author "{author}" `
  --task-slug project-info-task-start-rule `
  --task-name "项目大脑任务启动规则" `
  --request "建立任务目录和 workstate 规则" `
  --goal "让新会话能根据 workstate 恢复当前任务"
```

完成后读取根目录 `workstate.json`，确认 `task_folder` 指向新任务目录；新任务目录应包含 `process.md`、`design.md`、`任务范围.md` 和 `任务指标.md`。
