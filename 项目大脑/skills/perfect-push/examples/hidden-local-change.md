# 场景：工作区"看起来干净"但有隐藏改动

最容易漏的场景。`git status` 显示干净，实际有被标记隐藏的本地改动，rebase/reset/stash 时会丢。本场景对应回归用例 `hidden_local_change_blocks`。

## 现象

```powershell
git status --short
# 输出为空，看起来干净。
```

但预检脚本输出：

```json
{
  "can_push_main": false,
  "main_push": null,
  "blockers": ["hidden_index_flags"],
  "hidden_index_flags": ["README.md"],
  "status_summary": { "staged": 0, "unstaged": 0, "untracked": 0 }
}
```

- `status_summary` 全 0（`git status` 看不见），但 `hidden_index_flags` 列出了被隐藏的文件。
- 原因：有人对文件设过 `git update-index --assume-unchanged` 或 `--skip-worktree`，让 git 假装它没变，但磁盘内容其实改了。

## 正确操作序列

```powershell
# 1. 用 ls-files -v 看隐藏标记。小写 h = assume-unchanged，大写 S = skip-worktree。
git ls-files -v | findstr /R "^[hS]"

# 2. 清掉隐藏标记，让改动重新可见。
git update-index --no-assume-unchanged README.md
# 如果是 skip-worktree：
git update-index --no-skip-worktree README.md

# 3. 现在改动重新出现在 git status 里，按真实意图处理：
#    - 该入库就 git add + commit；
#    - 不该入库就移到别的 worktree 或显式 git stash；
#    - 不要把它留在工作区里去做 rebase/push。

# 4. 重跑预检，确认 hidden_index_flags 清空。
python 项目大脑/skills/perfect-push/scripts/check_perfect_push.py --repo-root . --json
```

## 预期结果

- 清标记并处理改动后，`hidden_index_flags` 变成空列表。
- `status_summary` 反映真实状态（干净或已提交）。
- 不再有 `hidden_index_flags` blocker。

## 要点

- `git status` 干净 ≠ 工作区真的干净。预检脚本额外查 `git ls-files -v` 的 `h` / `S` 标记，就是为了挡住这种"隐形改动"。
- 隐藏改动在 rebase / reset / stash / checkout 切换时极易丢失，必须先暴露再处理。
- 不要用 `--assume-unchanged` 来"跳过"一个改了的文件；它只是性能提示，不是"忽略改动"。
