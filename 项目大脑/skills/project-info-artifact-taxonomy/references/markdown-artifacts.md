# Markdown 工件职责表

本文保存项目大脑 Markdown 工件的完整职责 taxonomy。日常启动不要全量读取；只有新增、迁移、重命名、审核或排查工件职责时按需读取。

## 常驻骨架来源

`项目大脑/主规则.md` 只保留架构骨架和硬边界；本文件保存完整路径、用途和写入规则。

## 完整职责表

| 文件名 | 路径 | 规则 |
| --- | --- | --- |
| `AGENTS.md` | `AGENTS.md` | 全仓库 agent 短入口；只写项目大脑入口、项目概述、常用目录和重要文档索引。 |
| `agent工作规则.md` | `docs/agent工作规则.md` | 长背景说明；放项目背景、架构图、开发命令和数据表规则，不替代短入口。 |
| `CLAUDE.md` | `CLAUDE.md` | Claude Code 兼容入口；只导入 `AGENTS.md`，不复制规则正文。 |
| `.cursor/rules/project-rules.mdc` | `.cursor/rules/project-rules.mdc` | Cursor 兼容入口；只指向共享入口，不复制规则正文。 |
| `.github/copilot-instructions.md` | `.github/copilot-instructions.md` | Copilot 兼容入口；只指向共享入口，不复制规则正文。 |
| `主流程.md` | `项目大脑/主流程.md` | Runtime Hub：必读加载链、§2 硬规则速查、条件加载总表、重载规则；不写规则详情正文。 |
| `主规则.md` | `项目大脑/主规则.md` | Rules Detail Pack：L3 硬规则详情与架构骨架；冷启动不读全文，按需读取。 |
| `非技术人员操作安全护栏.md` | `项目大脑/非技术人员操作安全护栏.md` | 非程序员成员的强制简化操作安全护栏；由适用成员 `私有流程.md` 在 `私有规则.md` 前加载。 |
| `项目大脑/index.md` | `项目大脑/index.md` | 公共目录索引；只写链接和一句话用途，由 `tech-lead` 收口。 |
| `索引/` | `项目大脑/索引/`、`项目大脑/用户/{author}/索引/` | 公共或私人二级索引目录；一级 `index.md` 即将超过或已经超过 50 条时自动建立，按稳定主题归类，不一条文档一个索引。 |
| `代码地图/` | `项目大脑/代码地图/`、`项目大脑/用户/{author}/代码地图/` | 公共或私人代码查询规划知识库；搜索代码前先读私人再读公共，产出查询规划卡后带地图锚点用 CodeGraph / 限定目录 `rg` 验证；稳定结论只更新当前 author 私人地图和差异列表，记录路径和符号，不记录行号。 |
| `资料地图/` | `项目大脑/资料地图/`、`项目大脑/用户/{author}/资料地图/` | 公共或私人项目资料位置知识库；搜索文档、配置、脚本、表格、静态资源、会议记录、设计思路或外部资料前先读私人再读公共，普通搜索后只更新当前 author 私人资料地图。 |
| `说明.md` | `项目大脑/说明.md` | 设计指导思想和 Markdown 说明正文；与 `说明.html` 保持同一口径，不作为日常启动入口。 |
| `初始分工表.md` | `项目大脑/初始分工表.md` | 成员账号、昵称、初始职责和边界参考；不替代私人 `职责范围.md`。 |
| `工具经验.md` | `项目大脑/工具经验.md` | 系统工具经验入口；只收高价值、可复用、有解决方案的工具经验。 |
| `第三方辅助工具.md` | `项目大脑/第三方辅助工具.md` | CodeGraph、cc-artifact、MCP 适配等低频辅助工具入口；不保存 token、cookie 或本机私密配置。 |
| `公理/` | `项目大脑/公理/` | **已废止**（仅 `index.md` stub）；规则与设计原则见 `docs/规则/`。 |
| `docs/规则/` | `docs/规则/` | 入库代码规范、协作规则、领域设计原则；`index.md` 场景路由，分册按需点读。 |
| `私有流程.md` | `项目大脑/用户/{author}/私有流程.md` | 当前 author 私有加载流程；公共 `主流程.md` 之后读取，只写加载顺序和条件加载入口。 |
| `私有规则.md` | `项目大脑/用户/{author}/私有规则.md` | 当前 author 长期私有规则；只补充私人区和任务区规则，不覆盖公共 `主规则.md`。 |
| `协作灵魂.md` | `项目大脑/用户/{author}/协作灵魂.md` | 当前 author 私人协作软基调；不在项目大脑根目录建立公共版。 |
| `沟通规范.md` | `项目大脑/用户/{author}/沟通规范.md` | 当前 author 私人沟通规范；团队硬规则进入本文、公共 skill 或 docs。 |
| `docs/meeting/*.md` | `docs/meeting/*.md` | 团队共享会议转写清洗稿；保留来源、摘要、结论、行动项和待确认问题。 |
| `docs/会议转写术语表.md` | `docs/会议转写术语表.md` | 人名、昵称、不常见易错词和常见错写纠错表；只写稳定易错词。 |
| `用户/{author}/index.md` | `项目大脑/用户/{author}/index.md` | 当前 author 私人索引；默认只放私人入口，用户主动加入的常用公共资料可追加。 |
| `职责范围.md` | `项目大脑/用户/{author}/职责范围.md` | 当前 author 职责、允许修改区域、禁止事项和受保护文件；改文件前必须查看。 |
| `用户画像.md` | `项目大脑/用户/{author}/用户画像.md` | 当前 author 长期稳定身份、昵称和识别上下文；不写隐私或猜测性格。 |
| `工作习惯.md` | `项目大脑/用户/{author}/工作习惯.md` | 当前 author 稳定工作方式、验收偏好和工具习惯；不覆盖当次指令。 |
| `沟通偏好.md` | `项目大脑/用户/{author}/沟通偏好.md` | 当前 author 称呼、别名和沟通细节偏好；稳定沟通结构写私人 `沟通规范.md`。 |
| `经验/` | `项目大脑/用户/{author}/经验/` | 当前 author 私人经验主题目录；可包含历史教训、方法、索引、按需加载指南、checklist 或模板；由 `summarize-private-experience` 按主题聚类写入并同步私人索引，不一条经验一个文件。 |
| `私人整理状态.md` | `项目大脑/用户/{author}/经验/私人整理状态.md` | 当前 author 私人经验整理节流状态和 running 锁；只由 `private-experience-curation` 检查、抢锁和标记。 |
| `leader整理状态.md` | `项目大脑/用户/tech-lead/经验/leader整理状态.md` | `tech-lead` leader 公共抽取节流状态和 running 锁；只由 `leader-public-curation` 检查、抢锁和标记。 |
| `整理确认.md` | `项目大脑/用户/{author}/经验/整理确认.md` | 当前 author 私人整理后的求证文档；列出高把握整理点、低把握待确认点、依据原则、未收录项和需要使用者指导的问题。 |
| `leader整理确认.md` | `项目大脑/用户/tech-lead/经验/leader整理确认.md` | `tech-lead` 公共抽取后的求证文档；用于核对公共化是否正确、哪些判断不确定、哪些整理方法需要继续学习。 |
| `收件/` | `项目大脑/用户/{author}/收件/` | 当前 author 来自其他成员的收件目录；类似邮件，用于项目内快速同步当日重要信息；默认 md 格式，文件名带日期。 |
| `YYYY-MM-DD-{topic}.md` | `项目大脑/用户/{author}/收件/YYYY-MM-DD-{topic}.md` | 收件文件；文件头（至 `---` 分隔线）启动时可读，须含 `状态`（待处理/已处理）；已处理须精简文件头；正文须等收件人确认后再读；见 `project-inbox-rules`。 |
| `tasklist.md` | `项目大脑/用户/{author}/tasklist.md` | 当前 author 私人任务索引；任务目录是唯一主键，不同 author 不合并。 |
| `tasks/` | `项目大脑/用户/{author}/tasks/` | 当前 author 任务根目录；正文写到具体 task 子目录。 |
| `process.md` | `项目大脑/用户/{author}/tasks/{task-id}/process.md` | 记录过程、风险、验证、下一步和用户原始需求；不替代设计决策。 |
| `design.md` | `项目大脑/用户/{author}/tasks/{task-id}/design.md` | 记录稳定设计决策、原因、约束和被推翻旧口径；不写流水。 |
| `任务范围.md` | `项目大脑/用户/{author}/tasks/{task-id}/任务范围.md` | 记录原子工作项、状态、验证方法和证据；最多一个 `in_progress`。 |
| `任务指标.md` | `项目大脑/用户/{author}/tasks/{task-id}/任务指标.md` | 记录阶段验收、失败归因、上下文重建耗时和范围漂移。 |
| `handoff.md` | `项目大脑/用户/{author}/tasks/{task-id}/handoff.md` | 长会话、换人、换窗口时的短交接摘要。 |
| `交接说明.md` | `项目大脑/用户/{author}/tasks/{task-id}/交接说明.md` | 中文命名的任务交接摘要；用途与 `handoff.md` 相同，用于新会话、换人或阶段移交。 |
| `clean-state-checklist.md` | `项目大脑/用户/{author}/tasks/{task-id}/clean-state-checklist.md` | 收尾检查清单；每项必须可验证。 |
| `evaluator-rubric.md` | `项目大脑/用户/{author}/tasks/{task-id}/evaluator-rubric.md` | 阶段验收或里程碑评审口径；标准要能执行。 |
| `quality-document.md` | `项目大脑/用户/{author}/tasks/{task-id}/quality-document.md` | 质量快照；评级必须有证据。 |
| `给{recipient}的对齐说明.md` | `项目大脑/用户/{author}/tasks/{task-id}/给{recipient}的对齐说明.md` | 任务内给指定成员的对齐说明；只记录当前任务相关的边界、建议、待确认问题和依据，接收方确认前不生效为公共规则或私人规则。 |
| `YYYY-MM-DD-{topic}.md` | `项目大脑/用户/{author}/tasks/{task-id}/YYYY-MM-DD-{topic}.md` | 任务内日期补充记录；必须被 `process.md`、`design.md`、`任务范围.md` 或 `任务指标.md` 引用，只保存当前任务阶段补充、证据或交付摘要，稳定团队知识应沉淀到 `docs/`、私人经验或资料地图。 |
| `ARCHITECTURE.md` | `src/backend/ARCHITECTURE.md`、`src/frontend/ARCHITECTURE.md`、`services/example/ARCHITECTURE.md` | 模块职责、边界、入口、关键流程和依赖说明。 |
| `CONSTRAINTS.md` | `src/backend/CONSTRAINTS.md`、`src/frontend/CONSTRAINTS.md`、`services/example/CONSTRAINTS.md` | 模块硬约束；每条写必须/禁止、原因和违反后果。 |
