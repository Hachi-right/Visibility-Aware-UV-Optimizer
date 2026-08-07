# 任务启动规则

## 使用时机

当用户的请求已经明确到一个需要落盘跟踪的任务时，使用本 skill 创建当前任务目录。

创建新任务前，先读取 `项目大脑/用户/{author}/tasklist.md`，判断是否已有相似任务可以继续或 fork。如果用户要继续旧任务，不使用本 skill 新建目录，改用 `resume-project-task`。如果用户要基于旧任务派生新任务，先读取 `fork-project-task`；本 skill 只作为它在任务分支内创建新任务时调用的底层能力。

## 产物

| 产物 | 作用 |
|------|------|
| `项目大脑/用户/{author}/tasks/{YYYY-MM-DD}-{task-slug}/` | 当前任务目录 |
| `process.md` | 记录用户原始需求、目标、过程和验收摘要 |
| `design.md` | 记录本任务形成的设计决策 |
| `任务范围.md` | 任务内原子项、状态、验证步骤和证据 |
| `任务指标.md` | 当前任务的轻量度量，用于记录验证缺口、失败归因和范围漂移 |
| `workstate.json` | 将当前 worktree 指向当前任务目录 |
| `项目大脑/用户/{author}/tasklist.md` | 追加任务摘要和任务目录索引 |

## 参数

| 参数 | 含义 |
|------|------|
| `--author` | 当前远程账号名；未传入时读取根目录 `workstate.json.author` |
| `--task-name` | 任务可读名称 |
| `--task-slug` | 任务目录中的 ASCII slug |
| `--request` | 用户原始需求 |
| `--goal` | 本任务目标 |
| `--fork-from-task-folder` | 从已存在旧任务复制任务目录内容到新任务目录 |
| `--replace-current` | 用户确认开启新任务后，允许替换已有具体任务指针 |

## process.md 开头

`process.md` 开头必须先写用户原始需求和目标。新会话恢复时，先读这两段，再继续任务。

## design.md 开头

`design.md` 开头必须说明它只记录稳定设计决策。过程、尝试和临时判断继续写入 `process.md`。

## 四件套就绪：认知路由（团队经验 + 个人经验 + 团队公理）

四件套创建完成后、推进主工作项前，agent 须依次完成：

1. **团队经验**：读 `项目大脑/团队经验/INDEX.md`，按 request/goal 点读 1–3 篇 `entries/`，写入 `process.md`「团队经验引用」，将 `任务范围.md` 中 `team-exp-routing` 标为 `passing`。
2. **个人经验**：读 `项目大脑/用户/{author}/经验/INDEX.md`，按 request/goal 点读 0–3 篇 `entries/`，写入 `process.md`「个人经验引用」，将 `personal-exp-routing` 标为 `passing`；尚无 entries 时在关联说明写「暂无」。
3. **团队公理**：读 `项目大脑/公理/index.md`「任务开始路由」，按 request/goal 点读 0–2 篇 `entries/`，写入 `process.md`「团队公理引用」，将 `team-axiom-routing` 标为 `passing`；无匹配时在关联说明写「暂无」。

详见 `主流程.md` §3 与 `create-project-worktree/references/worktree.md`。

## 任务范围.md

`任务范围.md` 是固定表格格式的任务内范围清单。默认含 `team-exp-routing`（首项 WIP）、`personal-exp-routing`、`team-axiom-routing` 与 `task-objective`；后续可以拆成多个原子项，但同一时间最多一个 `in_progress`。

工作项表固定列为：

- `id`
- `标题`
- `状态`
- `行为`
- `验证方法`
- `证据`
- `备注`

状态只使用 `not_started`、`in_progress`、`blocked`、`passing`。

## 任务指标.md

`任务指标.md` 用表格记录长期指标：声称完成、实际验收、失败归因、重建上下文耗时、范围漂移和证据。过程细节仍写 `process.md`。

## tasklist.md 条目

新建任务后，脚本会向用户区 `tasklist.md` 追加一行。摘要应足够短，帮助后续启动时判断“当前需求是否是继续旧任务”。

`tasklist.md` 是当前 author 私人任务索引。不同 author 的 `tasklist.md` 分开放置；同一 author 多 worktree 合并时，以任务目录路径为唯一主键，保留双方新增的不同行。

## fork 旧任务

fork 旧任务时，脚本先把源任务目录内容复制到新任务目录根部，再重写新任务 `process.md` 和 `design.md` 顶部，使当前任务目标位于最前面。源任务的 `process.md` / `design.md` 会保留在同文件下方快照区，启动流程仍按普通任务只读取当前任务目录的 `process.md`、`design.md`、`任务范围.md` 和 `任务指标.md`。

复制时跳过本机私有目录、历史遗留派生目录、源任务 `任务范围.md` 和源任务 `任务指标.md`，避免私有材料、旧状态或旧指标传播。后续记录只写新任务自身文件，不回写旧任务。通常由 `fork-project-task` 判断何时传入 `--fork-from-task-folder`。
