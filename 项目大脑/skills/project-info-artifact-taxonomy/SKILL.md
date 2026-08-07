---
name: project-info-artifact-taxonomy
description: 判断项目大脑 Markdown 工件职责、内容落点、入口/索引/任务/地图/经验边界，或新增、改名、迁移项目大脑文档时使用。
---

# project-info-artifact-taxonomy

本 skill 保存项目大脑 Markdown 工件职责 taxonomy。日常启动读 `主流程.md` §2 硬规则速查；架构骨架按需读 `主规则.md` §架构骨架；只有要判断具体文件职责、调整工件结构、迁移文档或健康检查提示工件覆盖问题时，才读取本 skill 完整表。

## 使用场景

- 新增、改名、迁移或删除项目大脑 Markdown 文档。
- 不确定某类内容应该写入入口、索引、任务、经验、代码地图、资料地图、docs 还是模块文档。
- 修改 `AGENTS.md`、`启动.md`、`主流程.md`、`主规则.md`、公共 `index.md`、`说明.md` 或说明页。
- 项目大脑健康检查提示 Markdown 工件覆盖、入口链、索引职责或说明页同步问题。
- 整理项目大脑结构时，需要确认“不一条文档一个索引”“不重复建同职责文档”等边界。

## 必读参考

| 文件 | 内容 |
| --- | --- |
| `references/markdown-artifacts.md` | 项目大脑 Markdown 工件完整职责表，包含路径、用途和写入边界。 |

## 读取方式

1. 先读 `主规则.md` §架构骨架（或 `主流程.md` §2 速查），确认入口、索引、任务、地图和私人区的硬边界。
2. 只有需要具体落点或完整文件职责时，再读 `references/markdown-artifacts.md`。
3. 形成新规则或新工件后，按 `project-info-healthcheck` 验证入口链和工件覆盖。
