# perfect-push 常见 Git 安全问题

本文只记录 Git 更新和 push main 时会导致本地改动丢失、远端新改动被覆盖、或用错误 ref 做判断的问题。

| 问题 | 典型表现 | 固定处理 |
| --- | --- | --- |
| 未 fetch 最新 main | 本地 `origin/main` 仍是旧缓存，main 已被别人推进 | 预检默认强制 `git fetch origin +refs/heads/main:refs/remotes/origin/main`；`--no-fetch` 必须阻断。 |
| `origin/main` 不是 HEAD 祖先 | push 会被拒，或说明还没整合远端新改动 | 先 `git rebase refs/remotes/origin/main`，解决冲突后重新验证。 |
| 本地工作区 dirty | 未提交改动可能在 rebase/reset/stash 过程中丢失 | 精确 stage + commit；不入库的改动移到其它 worktree 或显式 stash。 |
| 隐藏本地改动 | `assume-unchanged` / `skip-worktree` 让 `git status` 看起来干净 | 预检检测 `git ls-files -v` 的 `h` / `S` 标记，先清掉标记再验证。 |
| ref shadow | 本地分支叫 `origin/main` 或 `origin/<branch>`，遮蔽远端 tracking ref | 所有远端判断使用完整 `refs/remotes/<remote>/<branch>`；发现 shadow 直接阻断。 |
| replace refs | `refs/replace/*` 伪造提交祖先关系 | 检测 replace refs，并用 `GIT_NO_REPLACE_OBJECTS=1` 复算 ancestry。 |
| Git 操作进行中 | rebase/merge/cherry-pick/revert 留在半完成状态 | 阻断 push；先继续或 abort，再跑验证和预检。 |
| 同文件冲突只保留一侧 | rebase 后 `origin/main` 已是祖先，但 main side 内容被手工覆盖 | 检测本地改动路径与最近 main 改动路径重叠，要求人工审查和重新验证。 |
| delete / rename | 删除或改名可能吞掉 main 上刚修改的文件 | `git diff --name-status -M origin/main..HEAD` 中出现 delete/rename 先阻断。 |
| binary diff | 二进制文件无法用文本 diff 检查是否丢 side | 阻断并要求人工审查。 |
| 功能分支远端有 remote-only commits | 本地功能分支落后或与远端同名分支分叉 | 先对齐功能分支；只有确认 remote-only 是本任务 rebase 前旧提交时才用 `--force-with-lease`。 |
| main push 被拒 | 有人在预检后又推进了 main | 停止；重新 fetch、rebase、验证、预检，不改用 force。 |

不在本表范围内的问题：作者身份、提交风格、diff 体量、payload 大小、业务设计质量、历史 healthcheck 全局债务。
