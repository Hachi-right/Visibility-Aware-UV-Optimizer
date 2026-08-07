# workstate 规则

## 使用时机

当项目大脑需要冷启动、刷新当前 author、或把当前 worktree 指向某个已存在任务目录时，使用本 skill。

## 字段

| 字段 | 含义 |
|------|------|
| `author` | 当前操作者的远程账号名，也是 `项目大脑/用户/{author}/` 的目录名 |
| `task_folder` | 当前任务目录；冷启动且尚未选择任务时为空字符串 |
| `git_user_name` | 当前 worktree 应使用的 `git config user.name` |
| `git_user_email` | 当前 worktree 应使用的 `git config user.email`；未知时可为空 |
| `remote` | author 所属的 Git remote，默认 `origin` |
| `source` | 本次状态来源 |
| `updated_at` | 写入时间 |
| `note` | 本地状态说明 |

## author 来源

优先使用命令行 `--author`。未传入时，只读取根目录 `workstate.json.author`。

如果根目录 `workstate.json` 不存在，或其中没有可信 author，应由用户确认当前远程账号名，再显式传入 `--author`。

仓库地址只用于定位仓库，不作为多人协作场景下的 author 来源；不要从项目归属 namespace 推导 author。

## task_folder 规则

`task_folder` 为空表示当前 worktree 尚未绑定具体任务。非空时必须是已存在目录，并且位于 `项目大脑/用户/{author}/` 下。

脚本写入仓库根目录 `workstate.json`。该文件是本机状态，由 `.gitignore` 忽略。

脚本写出使用 UTF-8；读取时接受 UTF-8 BOM，避免 Windows PowerShell 或其他外部工具生成的状态文件导致冷启动失败。
