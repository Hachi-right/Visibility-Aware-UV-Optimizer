# 场景：指向已有任务

当前 worktree 已经有明确任务，任务目录也已存在。

```powershell
$S = "项目大脑/skills/generate-workstate/scripts"
python "$S/generate_workstate.py" `
  --author "{author}" `
  --task-folder "项目大脑/用户/{author}/tasks/YYYY-MM-DD-example/" `
  --source task-resumed
```

完成后，`workstate.json.task_folder` 指向这个任务目录。
