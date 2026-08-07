---
name: init-project-info-user
description: 需要初始化项目大脑用户区、创建或校验 `项目大脑/用户/{author}/`，或冷启动时发现当前 author 没有用户目录时使用。
---

# init-project-info-user

使用本 skill 创建当前 author 的用户基础结构。不复制 `_template` 目录，不生成个人设计 HTML；完整设计审阅只保留根目录 `项目大脑/说明.html`。

## 生成内容

| 产物 | 作用 |
| --- | --- |
| `项目大脑/用户/{author}/` | 当前 author 的个人区根目录 |
| `项目大脑/用户/{author}/私有流程.md` | 公共主流程之后的当前 author 私有加载流程 |
| `项目大脑/用户/{author}/私有规则.md` | 当前 author 的长期私有规则；不覆盖公共主规则 |
| `项目大脑/用户/{author}/index.md` | 当前 author 的私人目录索引；默认只放私人区入口，用户主动要求加入的常用公共资料或 `docs/` 文档才追加 |
| `项目大脑/用户/{author}/职责范围.md` | 当前 author 的职责、允许修改边界、协作边界、受保护文件权限和昵称/转写别名；初次内容参考 `项目大脑/初始分工表.md` |
| `项目大脑/用户/{author}/用户画像.md` | 当前 author 的长期稳定背景、昵称和协作上下文；初始内容只写已确认事实 |
| `项目大脑/用户/{author}/工作习惯.md` | 当前 author 的稳定工作方式、验收偏好和协作节奏；初始化为可补充模板 |
| `项目大脑/用户/{author}/协作灵魂.md` | 当前 author 与 agent 协作时的软基调；初始化为可补充模板 |
| `项目大脑/用户/{author}/沟通规范.md` | 当前 author 与 agent 协作时的稳定沟通规范；初始化为可补充模板 |
| `项目大脑/用户/{author}/沟通偏好.md` | 当前 author 的沟通偏好、称呼和会议转写别名；初始化为可补充模板 |
| `项目大脑/用户/{author}/经验/` | 当前 author 从任务中沉淀的新知识、经验、规则、教训、方法、索引和 checklist，按主题聚类文档保存 |
| `项目大脑/用户/{author}/代码地图/` | 当前 author 私人代码查询规划层；搜索代码前先查私人再查公共，产出查询规划卡后带锚点验证，稳定经验按主题更新，并用 `差异列表.md` 记录与公共地图的差异 |
| `项目大脑/用户/{author}/资料地图/` | 当前 author 私人非代码资料定位知识库；搜索文档、配置、脚本、表格、静态资源、会议记录或设计思路前先查 |
| `项目大脑/用户/{author}/tasklist.md` | 当前 author 的任务索引；启动新任务前用于查找可继续的相似任务 |
| `项目大脑/用户/{author}/tasks/` | 当前 author 的任务根目录；具体任务子目录由任务 skill 在任务明确后创建 |
| `项目大脑/用户/{author}/skills/` | 当前 author 私有 skill 根目录 |
| `项目大脑/用户/{author}/mcps/` | 当前 author 私有 MCP 说明或配置根目录 |
| `项目大脑/用户/{author}/hooks/` | 当前 author 私有 hook 说明或配置根目录 |

本 skill 负责用户基础结构：用户根目录、`私有流程.md`、`私有规则.md`、根目录唯一一级 `index.md`、职责范围、私人软上下文、`经验/`、`代码地图/`、`资料地图/`、`tasklist.md`、`tasks/`、`skills/`、`mcps/` 和 `hooks/`。它不创建个人设计 HTML，不创建具体 task 子目录。`私有流程.md` 承接公共 `主流程.md`，只写当前 author 的加载顺序；必读链路只读取 `私有规则.md` 和当前任务上下文。私人 `index.md` 是当前 author 的私人文献索引，可能持续增长，只在需要私人资料、经验、方法、交付规则或常用文档时按需查询相关条目，再读取被索引文档；不要把私人 `index.md` 或 `经验/` 目录作为冷启动必读全文。私人一级 `index.md` 即将超过或已经超过 50 条入口时，使用 `project-index-maintenance` 自动分级到 `索引/{category}.md`。私人 `代码地图/` 是当前 author 的代码查询规划层；搜索代码前先读私人地图和 `差异列表.md`，再读公共地图，并在第一轮工具查询前形成查询规划卡；CodeGraph / `rg` 只负责带地图锚点验证源码，稳定入口和查找经验按主题写回当前 author 私人地图，公共缺失或不一致时写入 `差异列表.md`，记录路径和符号，不记录行号。私人 `资料地图/` 是当前 author 的非代码资料定位知识库；搜索文档、配置、表格、脚本、静态资源、会议记录或设计思路前先读私人资料地图，再读公共资料地图；普通搜索后只写当前 author 私人资料地图。`私有规则.md` 只写当前 author 的长期私有规则，不覆盖公共 `主规则.md`；每个 author 的经验不同，任务总结触发后默认写当前 author 私人 `经验/` 并更新当前 author 私人 `index.md`，供后续按需查阅；遇到特别大的需求、边界不清、跨模块或评估复杂的目标时，先拆阶段、定验收，再一阶段一阶段推进。`用户画像.md`、`工作习惯.md`、`协作灵魂.md`、`沟通规范.md`、`沟通偏好.md` 是借鉴“预制菜”结构后的轻量模板，用来初始化当前 author 的私人软上下文；`经验/` 对应私人公理/教训/方法沉淀，由 `summarize-private-experience` 按主题聚类写入分类文档，不一条经验一个文件。内容只来自 `初始分工表.md`、任务总结或占位提示，不复制外部私人内容。后续学习用户习惯时使用 skill `项目大脑/skills/learn-user-habits/`。新 skill / MCP / hook 默认先放当前 author 的 `skills/`、`mcps/`、`hooks/` 私有目录。只有当前任务明确后，才使用 `start-project-task` 或 `create-project-worktree` skill 创建 `tasks/{task-id}/process.md` 和 `design.md`。

初始化 `私有流程.md` 时，脚本会读取 `初始分工表.md` 的 `角色类型`：角色类型不含“程序”且不含“技术策划”的 author，会在必读加载流程第 1 行加入 `项目大脑/非技术人员操作安全护栏.md`，再加载 `私有规则.md` 和当前任务上下文；`team-lead` 按技术策划处理，不自动加入该护栏。

`初始分工表.md` 只用于初始化 `职责范围.md`。若 author 为历史别名，先查 `项目大脑/账号别名.md` 解析到 canonical 用户区后再初始化。日常修改边界以用户私有 `职责范围.md` 为准；明显越界时先拒绝并说明风险，用户坚持时再确认是否修改职责范围/修改边界；当前 author 可以主动同步已达成共识的交付规则文档到自己的职责范围；当前 author 可以维护自己的职责范围文件。

## 脚本表

| 脚本 | 用途 |
| --- | --- |
| `scripts/init_user.py` | 创建或校验当前 author 的用户基础结构 |
| `scripts/author_aliases.py` | 解析历史 author 别名到 canonical 用户区；与 `项目大脑/账号别名.md` 保持同步 |

## 使用方式

```powershell
python 项目大脑/skills/init-project-info-user/scripts/init_user.py `
  --author "{author}"
```

未传 `--author` 时，脚本读取根目录 `workstate.json.author`。
