# 场景：从 main 开任务 worktree

当前在 `main` 或其他基础分支，用户明确要开始一个会修改仓库文件的新任务。

```powershell
$S = "项目大脑/skills/create-project-worktree/scripts"
python "$S/create_project_worktree.py" `
  --author "{author}" `
  --task-slug project-info-example `
  --task-name "项目大脑示例任务" `
  --request "用户原始需求" `
  --goal "本任务目标"
```

完成后进入脚本输出的新 worktree 目录，重新打开会话或继续执行：

```powershell
git status --short --branch
Get-Content workstate.json
```

新会话从 `AGENTS.md -> 项目大脑/启动.md -> 项目大脑/主流程.md -> 项目大脑/主规则.md` 继续；新任务目录应包含 `process.md`、`design.md`、`任务范围.md` 和 `任务指标.md`。启动后先完成 `team-exp-routing`、`personal-exp-routing`、`team-axiom-routing`（团队经验 INDEX → 1–3 篇；个人经验 INDEX → 0–3 篇；团队公理 index 任务开始路由 → 0–2 篇；分别写入 `process.md` 对应引用段），再推进 `task-objective`。

创建脚本会在新 worktree 根目录强制执行 `scripts/init_codegraph_for_worktree.bat`，由 bat 调用 `codegraph init .`。成功后新 worktree 应出现被 `.gitignore` 忽略的 `.codegraph/` 本机缓存目录。
