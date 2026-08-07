# 场景：本地分支叫 origin/main，遮蔽了远端 tracking ref

ref shadow 场景。短名 `origin/main` 可能解析到本地分支而非远端跟踪 ref，导致基于错误 ref 判断。本场景对应回归用例 `ambiguous_origin_main_ref_blocks`、`feature_branch_remote_ref_shadow`。

## 现象

预检脚本输出：

```json
{
  "can_push_main": false,
  "main_push": null,
  "blockers": ["ambiguous_target_ref"],
  "ambiguous_target_refs": ["refs/heads/origin/main"],
  "target_full_ref": "refs/remotes/origin/main"
}
```

- `ambiguous_target_refs` 含 `refs/heads/origin/main`：存在一个叫 `origin/main` 的本地分支。
- `target_full_ref` 是 `refs/remotes/origin/main`：脚本始终用完整远端跟踪 ref 判断，所以结论可信。
- 功能分支也有同样问题：`ambiguous_feature_branch_remote_ref`，`ambiguous_branch_remote_refs` 含 `refs/heads/origin/task/xxx`。

含义：如果你用 `git rebase origin/main` 这种短名，git 可能解析到本地分支 `refs/heads/origin/main`，而不是远端的 `refs/remotes/origin/main`，基于错误 ref 做 rebase/push。

## 正确操作序列

```powershell
# 1. 脚本已经用完整 ref 判断，结论可信：先删掉遮蔽的本地分支。
#    确认这个本地分支不是你真正在用的分支（它只是名字撞车）。
git branch -d origin/main
# 如果有未合并提交且确认不需要，用 -D 强删；先核对再删。

# 2. 删掉后重跑预检，确认 ambiguous_target_ref 消失。
python 项目大脑/skills/perfect-push/scripts/check_perfect_push.py --repo-root . --json

# 3. 自己写命令时永远用完整 ref，不用短名：
git fetch origin +refs/heads/main:refs/remotes/origin/main
git rebase refs/remotes/origin/main
git diff --check refs/remotes/origin/main..HEAD
```

## 预期结果

- 删掉遮蔽分支后，`ambiguous_target_refs` 为空，`ambiguous_target_ref` blocker 消失。
- `target_full_ref` 始终是 `refs/remotes/origin/main`，不受本地分支影响。

## 要点

- 本地分支名不要取 `origin/main`、`origin/<branch>` 这种远端 tracking ref 的短名，极易遮蔽。
- 预检脚本对所有远端判断都用完整 refname `refs/remotes/<remote>/<branch>`，并对 main 和功能分支都做 shadow 检测，发现双 ref 直接阻断。
- 自己手敲命令时也用完整 ref，和脚本保持一致，避免短名解析歧义。
