---
name: project-validation
description: 项目任务验收流程。需要为代码、配置、文档或功能改动制定/执行验收时使用；覆盖 lint/格式检查、单元测试方法、单元测试基础设施、端到端集成模拟、运行日志分析，以及公共/私人测试 skill 和验收基础设施的放置规则。
---

# project-validation

本 skill 是任务完成验收入口。先按改动范围选择最小但足够的验收层级，再读取对应 reference 执行；验收结果写回当前任务 `process.md`，稳定验收口径写入当前任务 `evaluator-rubric.md`。

## 验收层级

| 层级 | 何时使用 | 必看 reference |
| --- | --- | --- |
| 轻量检查 | 文档、脚本、小范围非运行时代码改动 | `references/lint.md` |
| 单元测试 | 修改 Python/JS/配置导出/协议/【示例-service模块】 逻辑 | `references/unit-tests.md` |
| 功能触发 | 改动影响某个用户可见功能或主链路 | `references/unit-tests.md`，按改动补定向命令 |
| 完整模拟 | 改动影响多模块联动、核心业务链路或运行时日志 | `references/launcher-simulation.md`，`references/log-analysis.md` |
| 私人扩展 | 当前 author 有个人测试 skill、临时脚本或专属验收方法 | `references/private-validation.md` |

## 执行顺序

1. 读当前任务 `process.md` 和 `design.md`，确认用户目标、修改范围和停止条件。
2. 读 `references/lint.md`，执行格式、编译和代码规范基础检查。
3. 读 `references/unit-tests.md`，按改动范围选择根工程、【示例-service模块】、前端或数据导出测试。
4. 如果改动涉及项目大脑入口、主流程、公共 skill、任务工件规则或说明页，读取并运行 skill `项目大脑/skills/project-info-healthcheck/`。
5. 需要完整模拟时，读 `references/launcher-simulation.md`，按项目约定启动并触发被改功能。
6. 读 `references/log-analysis.md`，扫描本次启动段日志；必要时运行 `scripts/analyze_launcher_logs.py`。
7. 如果当前 author 私人 `index.md` 引用了私有测试 skill，读 `references/private-validation.md` 判断是否补跑。
8. 把命令、结果、日志结论、未覆盖风险写回当前任务 `process.md`；阶段验收标准写入 `evaluator-rubric.md`。

## Reference 表

| 主题 | 文件 | 内容 |
| --- | --- | --- |
| lint / 格式检查 | `references/lint.md` | `git diff --check`、Python 编译、前端 build、代码规范审计和依赖缺失处理 |
| 单元测试 | `references/unit-tests.md` | 根工程、【示例-service模块】、前端、数据导出测试选择方法 |
| 端到端集成模拟 | `references/launcher-simulation.md` | 按项目约定启停依赖服务、验证主链路和清理进程 |
| 日志分析 | `references/log-analysis.md` | 运行日志位置、阻塞错误、预期 warning、业务拒绝和脚本用法 |
| 私人验收扩展 | `references/private-validation.md` | 当前 author 私有测试 skill/脚本/基础设施的放置和调用规则 |

## 脚本表

| 脚本 | 用途 |
| --- | --- |
| `scripts/analyze_launcher_logs.py` | 扫描项目日志目录，汇总阻塞错误、业务动作拒绝和预期 warning |
