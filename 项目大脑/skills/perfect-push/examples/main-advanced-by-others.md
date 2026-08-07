# 场景：远端 main 被别人推进了（target_not_ancestor）

最常见、最容易处理错的场景。本场景对应回归用例 `target_not_ancestor`、`no_fetch_blocks`。

## 现象

预检脚本输出：

```json
{
  "can_push_main": false,
  "main_push": null,
  "blockers": ["target_not_ancestor"],
  "diff_metrics": { "files": 0 },
  "main_is_ancestor": false
}
```

- `main_is_ancestor=false`：`refs/remotes/origin/main` 不是当前 `HEAD` 的祖先。
- `diff_metrics.files=0`：因为本地还没整合远端新改动，`origin/main..HEAD` 看不到待推内容。
- 如果用了 `--no-fetch` 还会多一个 `fetch_skipped` blocker——跳过 fetch 不允许放行。

含义：有人在你开始工作后推进了 main。直接 push 会被拒，或误用 force 会覆盖同事的提交。

## 正确操作序列

```powershell
# 1. 不要 force。先确认拿到最新远端 ref。
git fetch origin +refs/heads/main:refs/remotes/origin/main

# 2. 用完整 ref 做 rebase，不要用 origin/main 短名（短名可能被本地分支遮蔽，见 ref-shadow 场景）。
git rebase refs/remotes/origin/main
```

rebase 出冲突时，逐文件理解双方意图：

- 本地侧是你本任务的新改动。
- main 侧是同事刚推进的新改动。
- 两侧都要保留，不要无脑 `git checkout --ours` / `--theirs`，否则会丢一边。

```powershell
# 3. 冲突解决后 stage 并继续。
git add <冲突文件>
git rebase --continue

# 4. 重跑任务相关验证 + 预检。验证证据会重新绑定到 rebase 后的新 HEAD。
python 项目大脑/skills/perfect-push/scripts/check_perfect_push.py --repo-root . `
  --validation-command "static::git diff --check refs/remotes/origin/main..HEAD" `
  --validation-command "integration::python -m py_compile <本轮改动的脚本>"
```

## 预期结果

- rebase 后 `main_is_ancestor=true`，`target_not_ancestor` 消失。
- `diff_metrics.files>0`，能看到待推内容。
- `can_push_main=true` 后再执行 `git push origin HEAD:main`。

## 要点

- 永远用完整 ref `refs/remotes/origin/main`，不用 `origin/main` 短名。
- rebase 解决冲突是"保留双方意图"，不是"选一边"。
- 如果 push main 仍被拒，说明预检后 main 又被推进了：重新 fetch、rebase、验证、预检，不改用 force。
