---
name: project-info-healthcheck
description: 项目大脑自检。需要检查 AGENTS/适配入口/启动/主流程/主规则入口链、workstate、用户区、当前任务工件、公共索引、skill reference/example/script 索引、Markdown 工件覆盖和说明页同步时使用。
---

# project-info-healthcheck

本 skill 用于验证项目大脑自身没有断链、孤岛文件、入口口径漂移或当前任务状态缺失。修改项目大脑入口文件、公共 skill、任务工件规则、说明页或启动流程后必须运行。

## 执行方式

```powershell
python 项目大脑/skills/project-info-healthcheck/scripts/healthcheck.py
```

## 检查范围

| 范围 | 内容 |
| --- | --- |
| 入口链 | `AGENTS.md`、`CLAUDE.md`、Cursor/Copilot 适配入口、`启动.md`、`主流程.md`、`主规则.md` |
| Agent hook | Codex/Cursor/Claude 的项目级 compact 或长会话提醒配置，以及共享 hook 脚本 |
| 本机状态 | 根目录 `workstate.json`、当前 author、当前任务目录 |
| 用户区 | `index.md` 私人索引边界、`职责范围.md`、职责越界拒绝规则、交付规则主动同步规则、私人软上下文、根目录不得放私人软上下文、`经验/`、`代码地图/` 与 `差异列表.md`、`资料地图/`、`skills/`、`mcps/`、`hooks/`、`tasklist.md`、`tasks/` |
| 当前任务 | `process.md`、`design.md`、`任务范围.md`、`任务指标.md` |
| 公共索引 | `项目大脑/index.md` 中的公共文档、公共 docs 入口、公共 skill，以及新 skill/MCP/hook 默认私有、公共区只走 leader 公共化的口径 |
| skill 文件 | 每个 public skill 的 `references/`、`examples/`、`scripts/` 都被父 `SKILL.md` 索引 |
| 工件覆盖 | 项目大脑相关 Markdown 都能在 `主规则.md` 归类到稳定规则；目录型工件要有自己的索引；一级索引超 50 条时自动分级而不报错；私人经验、公共公理、代码地图和资料地图必须按主题聚类；地图类文档不能记录行号；`主流程.md` 不能反向引用拉起它的入口文件 |
| 审阅页 | `说明.html` 包含关键工件和 skill 名称 |

## 脚本表

| 脚本 | 用途 |
| --- | --- |
| `scripts/healthcheck.py` | 执行项目大脑覆盖自检；发现错误返回非 0 |
