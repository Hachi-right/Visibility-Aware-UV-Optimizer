# worktree 创建规则

## author 来源

author 必须是当前操作者的远端账号名，并同时用于：

| 位置 | 用途 |
|------|------|
| Git identity | `git config user.name` |
| 冷启动状态 | `workstate.json.author`、`workstate.json.git_user_name` |
| 用户区 | `项目大脑/用户/{author}/` |

优先级：

1. 命令行 `--author`
2. 当前仓库根目录 `workstate.json.author`
3. Git credential 中当前远端的 username
4. 远端 URL 中显式携带的 username

脚本不从仓库 URL 的项目归属 namespace 推导 author。无法确认时，要求用户显式传入 `--author`。

## Git identity

创建 worktree 后，脚本会设置：

```text
git config user.name  {author}
git config user.email {email}
```

`email` 可用 `--email` 指定；未指定时默认使用 `{author}@kingsoft.com`。

## 任务目录产物

新 worktree 内会创建：

| 产物 | 作用 |
|------|------|
| `workstate.json` | 指向当前 author、Git identity 和当前 task folder，不入库 |
| `项目大脑/用户/{author}/tasklist.md` | 追加新任务摘要和目录索引 |
| `项目大脑/用户/{author}/tasks/{task-id}/process.md` | 过程、尝试、验收摘要 |
| `项目大脑/用户/{author}/tasks/{task-id}/design.md` | 稳定设计决策 |
| `项目大脑/用户/{author}/tasks/{task-id}/任务范围.md` | 任务内原子项、状态、验证步骤和证据 |
| `项目大脑/用户/{author}/tasks/{task-id}/任务指标.md` | 当前任务轻量指标和长期对比数据 |

## 四件套就绪：认知路由（团队经验 + 个人经验 + 团队公理）

四件套创建完成后、推进主工作项前，agent 须依次完成：

1. **团队经验**
   - 读 `项目大脑/团队经验/INDEX.md`「主动检索触发」
   - 按 `process.md` 中 request/goal 关键词点读 1–3 篇 `entries/`（禁止全量）
   - 在 `process.md`「团队经验引用」段填入条文 ID 与关联说明
   - 在 `任务范围.md` 将 `team-exp-routing` 标为 `passing`

2. **个人经验**
   - 读 `项目大脑/用户/{author}/经验/INDEX.md`
   - 按 request/goal 点读 0–3 篇 `entries/`（禁止全量；尚无 entries 时关联说明写「暂无」）
   - 在 `process.md`「个人经验引用」段填入条文 ID 与关联说明
   - 在 `任务范围.md` 将 `personal-exp-routing` 标为 `passing`

3. **团队公理**
   - 读 `项目大脑/公理/index.md`「任务开始路由」（只读路由表，不预读全部 entries 正文）
   - 按 request/goal 点读 0–2 篇 `公理/entries/`（禁止全量 8 篇）
   - 在 `process.md`「团队公理引用」段填入公理 ID 与关联说明（无匹配写「暂无」）
   - 在 `任务范围.md` 将 `team-axiom-routing` 标为 `passing`

两项均 `passing` 后再推进 `task-objective`。触发与边界见 `主流程.md` §3；与设计歧义时的 `design-advisor-lens` 分轨，不混读私人公理。

## CodeGraph 初始化

每个新 worktree 创建完成后，`create_project_worktree.py` 会调用同目录的 `scripts/init_codegraph_for_worktree.bat`，在新 worktree 根目录执行：

```powershell
codegraph init .
```

这是 worktree 创建流程的强制后置步骤，用来保证新 worktree 默认具备 CodeGraph 项目索引。`.codegraph/` 是本机缓存，必须保持在 `.gitignore` 中，不提交入库。

如果本机没有 `codegraph` 命令，或 `codegraph init .` 返回失败，创建脚本会以非 0 状态退出并打印 bat 输出；此时需要先修复本机 CodeGraph CLI，再重新创建或手工补初始化该 worktree。

## 从旧任务 fork

如果用户要基于旧任务快速启动新 worktree，先读取 `fork-project-task`，由它确认源任务和新任务目标；本脚本作为底层能力接收 `--fork-from-task-folder`。脚本会先校验旧任务目录属于当前 author，且包含 `process.md` 和 `design.md`；然后把源任务目录内容复制到新任务目录根部，再重写新任务 `process.md` / `design.md` 顶部，并生成新的 `任务范围.md` 和 `任务指标.md`，使当前任务目标位于最前面。源任务记录保留在同文件下方快照区，启动流程仍和普通任务一样。

复制时跳过本机私有目录、历史遗留派生目录、源任务 `任务范围.md` 和源任务 `任务指标.md`。后续记录只写新任务，不回写旧任务。

`tasklist.md` 是当前 author 私人任务索引。不同 author 的任务索引不合并；同一 author 多 worktree 冲突时，以任务目录路径为唯一主键，保留双方新增的不同行。

## 分支和目录命名

分支名默认：

```text
task/{author}-{YYYY-MM-DD}-{task-slug}
```

worktree 目录默认建在当前仓库父目录下：

```text
{repo-name}-{YYYY-MM-DD}-{task-slug}
```

如重名，脚本会追加 `-2`、`-3`。
