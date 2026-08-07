---
name: create-project-worktree
description: 需要从当前仓库创建任务 worktree，并把 Git identity、workstate.json.author、用户任务目录 author 统一锚定到远端账号时使用。
---

# create-project-worktree

## 参考文档表

| 主题 | 文件 | 内容 |
|------|------|------|
| worktree 创建规则 | `references/worktree.md` | author 解析、分支命名、Git identity、任务目录产物 |

## 示例表

| 场景 | 文件 | 内容 |
|------|------|------|
| 从 main 开任务 worktree | `examples/from-main.md` | 创建 worktree、写入 workstate、创建 process/design |
| 从旧任务 fork 到新 worktree | `examples/fork-from-main.md` | 创建新 worktree，并复制旧任务目录内容 |

## 脚本表

| 脚本 | 用途 |
|------|------|
| `scripts/create_project_worktree.py` | 创建任务 worktree，锚定 author / Git identity / workstate，写入任务默认工件，并追加用户 `tasklist.md` |
| `scripts/init_codegraph_for_worktree.bat` | 新 worktree 创建完成后强制执行 `codegraph init .`，生成本机 `.codegraph/` 索引缓存 |
