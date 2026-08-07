# 场景：干净功能分支推送 main

最顺路径的正例。先看清楚"一切正常时长什么样"，再去识别危险。本场景对应回归用例 `clean_missing_remote_branch`。

## 前提

- 当前在功能分支 `task/xxx`，改动已 `git commit`。
- 工作区干净，没有未提交或隐藏改动。
- `origin/main` 没有被别人推进（本地 `refs/remotes/origin/main` 是当前 `HEAD` 的祖先）。
- 已经跑过本轮任务的相关验证（静态检查、集成测试等）。

## 操作序列

```powershell
# 1. 跑只读预检，并让脚本当场执行验证命令、绑定到当前 HEAD。
#    static 类用 git diff --check，integration 类用 py_compile 或项目测试脚本。
python 项目大脑/skills/perfect-push/scripts/check_perfect_push.py --repo-root . `
  --validation-command "static::git diff --check refs/remotes/origin/main..HEAD" `
  --validation-command "integration::python -m py_compile 项目大脑/skills/perfect-push/scripts/check_perfect_push.py"
```

## 预期 JSON（关键字段）

```json
{
  "can_push_main": true,
  "blockers": [],
  "main_push": "git push origin HEAD:main",
  "branch_push": "git push -u origin task/xxx",
  "branch_remote_exists": false,
  "validation_status": { "valid": true, "errors": [] },
  "main_is_ancestor": true
}
```

- `can_push_main=true` 且 `blockers=[]`，说明可以推 main。
- `main_push` 是普通快进 push，不含 `--force`。
- `branch_remote_exists=false`，说明远端还没有同名功能分支，先用 `branch_push` 的 `-u` 建立跟踪。

## 推送

```powershell
# 2. 先推功能分支（建立远端跟踪）。
git push -u origin task/xxx

# 3. 再推 main（普通快进，不是 force）。
git push origin HEAD:main

# 4. 跑脚本输出的 post-push verify，确认本地 HEAD 和最新 origin/main 短哈希一致。
```

## 要点

- 验证证据必须由脚本当场绑定到当前 `HEAD`，不能靠聊天里说"已验证"。
- `main_push` 只能是普通 `git push <remote> HEAD:<target>`；只要它变成 `--force*`，就说明某条 hard gate 没满足，不能直接执行。
- 推 main 前先推功能分支，方便出问题时回退和审查。
