# 场景：已有任务时切换

当前 `workstate.json.task_folder` 已经指向一个具体任务。用户确认要开启新任务后，使用替换参数。

```powershell
$S = "项目大脑/skills/start-project-task/scripts"
python "$S/start_task.py" `
  --author "{author}" `
  --task-slug project-info-skill-packaging `
  --task-name "项目大脑脚本 skill 化" `
  --request "把 scripts 改成标准 skills" `
  --goal "让两个脚本作为标准 skill 资源被索引和复用" `
  --replace-current
```

完成后，旧任务目录保留，新任务目录成为当前 `task_folder`。
