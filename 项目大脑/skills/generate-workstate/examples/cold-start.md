# 场景：冷启动状态

首次进入 worktree，已经确认当前远程账号名，但还没有明确当前任务。

```powershell
$S = "项目大脑/skills/generate-workstate/scripts"
python "$S/generate_workstate.py" --author "{author}" --source cold-start
```

完成后检查仓库根目录 `workstate.json`：

```json
{
  "author": "{author}",
  "task_folder": "",
  "git_user_name": "{author}",
  "git_user_email": "",
  "remote": "origin",
  "source": "cold-start"
}
```
