---
name: perfect-push
description: 完成安全 push。用于把当前 HEAD 更新并推送到 main/master 前，验证 git fetch/rebase/conflict/push 链路不会丢失本地改动、不会覆盖远端新改动、不会用 stale ref 或伪装 ref 做判断。
---

# perfect-push

本 skill 的收束目标只有一个：保证 Git 更新与 push main 的过程安全。它不审查作者身份、提交风格、diff 体量或业务设计优劣；这些问题可以由其它流程处理，不能混入本 skill 的自动验收结论。

## 面向非技术人员的说法与默认目标

不要把 Git 术语直接抛给任何非技术人员。按用户的原话先复述，再操作：

| 用户说法 | 先复述 | 默认动作 |
| --- | --- | --- |
| “提交到分支” | “提交到分支是进行本地保存，不会提交到 main。” | 只做本地提交，不上传远端。 |
| “提交到 main” | “提交到 main 会保存本次改动并上传到远端，别人就都能看到了。” | 精确暂存当前 task 改动、提交，然后推送远端 main。 |
| “push main” / “推送 main” / “推送主干” | “将保存本次改动并上传到远端 main，别人随后就能看到。” | 精确暂存当前 task 改动、提交，然后推送远端 main。 |
| 只说“推送” | “默认会保存本次改动并上传到远端 main，别人随后就能看到。” | 精确暂存当前 task 改动、提交，然后推送远端 main。 |

推送后必须用远端实际状态回报，不能只看命令是否结束：

- `HEAD` 与重新获取的 `origin/main` 是同一提交：**“已经成功 push main，推送到远端了，别人可以看到了。”**
- 推送命令失败、远端 main 不存在本次 `HEAD`，或后验失败：**“没有成功推送远端，别人看不到。”**

上传到功能分支时必须明确说“只上传到了独立任务分支，远端 main 没有变化”，不得称为“已推送成功”。

## 快速预检

先运行只读预检脚本：

```powershell
python 项目大脑/skills/perfect-push/scripts/check_perfect_push.py --repo-root .
```

需要自动得到 `can_push_main=true` 时，必须把验证命令绑定到当前 HEAD。推荐直接让脚本执行验证命令：

```powershell
python 项目大脑/skills/perfect-push/scripts/check_perfect_push.py --repo-root . `
  --validation-command "static::git diff --check refs/remotes/origin/main..HEAD" `
  --validation-command "integration::python -m py_compile 项目大脑/skills/perfect-push/scripts/check_perfect_push.py 项目大脑/skills/perfect-push/scripts/test_check_perfect_push.py"
```

聊天里说“已验证”不算证据。验证证据必须对应当前 `HEAD` 和当前 `refs/remotes/origin/main`。

## 标准流程

1. 确认工作区干净；未提交内容要么精确提交，要么明确移走或 stash。
2. 运行本 skill 的预检脚本；不要跳过 fetch，也不要跳过 diff check。
3. 如果报告 `target_not_ancestor`，先执行：

   ```powershell
   git rebase refs/remotes/origin/main
   ```

4. rebase 有冲突时，逐文件理解双方意图，保留本地改动和 main 上的新改动；不要无脑 `--ours` / `--theirs`。
5. 冲突解决后重新跑任务相关验证和本 skill 预检。
6. 先按脚本建议推当前功能分支；只有确认远端同名功能分支的 remote-only commits 是本任务 rebase 前旧提交时，才允许对功能分支使用 `--force-with-lease`。
7. 只有 `can_push_main=true` 时才执行普通 main push：

   ```powershell
   git push origin HEAD:main
   ```

8. push main 后执行脚本输出的 post-push verify，确认 `HEAD` 与最新 `origin/main` 短哈希一致。

## Hard Gates

以下情况必须阻止 main push：

- 未 fetch 最新 `refs/remotes/origin/main`，或 fetch 失败。
- 当前分支是 detached HEAD，或 `--source` 不是当前 `HEAD`。
- 工作区 dirty，或存在 `assume-unchanged` / `skip-worktree` 隐藏本地改动。
- 目标不是 main/master，target/source 缺失，或 `origin/main` 不是 `HEAD` 祖先。
- 本地存在 `refs/heads/origin/main`、`refs/heads/origin/<branch>` 等 ref shadow。
- 存在 `refs/replace/*`，或 ancestry 在 `GIT_NO_REPLACE_OBJECTS=1` 下不一致。
- rebase、merge、cherry-pick、revert 等 Git 操作仍在进行中。
- 待推范围含 merge commit、binary diff、delete、rename、大小写冲突路径。
- 本地改动路径与最近 main 变更路径重叠，说明冲突解决可能丢掉了 main side。
- 缺少绑定当前 HEAD 的验证证据，或 `git diff --check origin/main..HEAD` 失败。
- 远端同名功能分支含 remote-only commits，必须先对齐功能分支。

## 有条件放行：recent_target_overlap

`recent_target_overlap` 阻断"本地改动路径与最近 main 变更路径重叠"，防止 rebase 冲突解决时丢掉 main side。当本轮任务就是改最近 main 也改过的文件（例如优化 perfect-push 自身），这条 gate 会持续阻断，构成自指悖论。

若已逐文件人工审查确认 rebase 没丢 main side，且其余所有安全 gate 通过，可用：

```powershell
python 项目大脑/skills/perfect-push/scripts/check_perfect_push.py --repo-root . `
  --acknowledge-recent-target-overlap `
  --validation-command "static::git diff --check refs/remotes/origin/main..HEAD" `
  --validation-command "integration::python -m py_compile <本轮改动脚本>"
```

该参数仅在以下条件全部满足时把 `recent_target_overlap` 降级为 warning：

- `blockers` 恰好只剩 `recent_target_overlap` 一条（其余所有 hard gate 通过）。
- 验证证据有效且绑定当前 `HEAD`。
- `origin/main` 是 `HEAD` 的祖先。
- 工作区干净，无进行中 Git 操作。

这是 R7 人工复核的一部分，不是凭空放行：必须先实际逐文件比对 rebase 结果，确认没丢 main side。JSON 输出 `safety_scope.overlap_acknowledged=true` 留痕。若还有任何其他 blocker，该参数无效，`recent_target_overlap` 仍在 `blockers` 中阻断。

## Out Of Scope

以下问题不属于本次 perfect-push 自动验收目的，不能作为 `can_push_main` 的 hard blocker：

- 作者/committer 身份、cherry-pick/revert/fixup/squash、空提交、提交时间顺序。
- diff 文件数、增删行、payload 大小、是否“保护路径”。
- 业务功能是否完善、文档风格是否好、healthcheck 的历史全局债务。

## 常见问题

完整经验见 `references/common-push-problems.md`。

## 示例

低能力模型调用本 skill 时，优先照着示例的命令和预期 JSON 操作。示例只演示写法，实际分支名和验证命令按当前任务替换。每个示例都对应一个回归用例，可在 `scripts/test_check_perfect_push.py` 里查到攻击构造。

| 场景 | 文件 | 内容 |
| --- | --- | --- |
| 干净功能分支推送 main | `examples/clean-feature-push.md` | 最顺路径正例：预检、看 JSON、先推功能分支再推 main、post-push verify。 |
| 远端 main 被别人推进 | `examples/main-advanced-by-others.md` | `target_not_ancestor` 时 fetch + rebase + 解冲突 + 重验，不 force。 |
| 隐藏本地改动 | `examples/hidden-local-change.md` | `git status` 干净但有 `assume-unchanged`/`skip-worktree` 时先暴露再处理。 |
| ref shadow | `examples/ref-shadow.md` | 本地分支叫 `origin/main` 遮蔽远端 ref 时删遮蔽分支、用完整 ref。 |

## 验收标准

- `can_push_main=true` 时必须无 blocker，且 `main_push` 只能是普通 `git push <remote> HEAD:<target>`。
- JSON 输出必须包含足够自动化判断的字段：`blockers`、`warnings`、`next_actions`、`safety_scope`、`commit_range.merge_commits`、`diff_metrics.renames/deleted_paths/case_collisions/recent_target_overlap_paths`、`validation_status`、`branch_remote_*`、`git_operation_state`、`hidden_index_flags`、`status_summary`。
- 回归测试必须在临时 bare remote 和临时 clone 中构造攻击，不允许改坏真实工作区。
- `scripts/test_check_perfect_push.py` 当前覆盖 27 个聚焦 Git 攻防场景：fresh target、dirty/hidden local change、target 非祖先、跳过 fetch/diff check、feature remote-only commits、ref shadow、source 非 HEAD、detached HEAD、replace refs、`.git` manifest、merge commit、rebase 进行中、同文件冲突丢 main side、delete/rename、大小写冲突路径，以及 `--acknowledge-recent-target-overlap` 的有条件放行与"不掩盖其他 blocker"。

## 脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/check_perfect_push.py` | 只读预检当前 HEAD 能否安全快进 main，输出 blocker、验证证据、分支 push / main push / post-push verify 建议。 |
| `scripts/test_check_perfect_push.py` | 创建临时 Git 远端和工作区，回归验证 25 个 Git 更新/push 冲突安全场景。 |
