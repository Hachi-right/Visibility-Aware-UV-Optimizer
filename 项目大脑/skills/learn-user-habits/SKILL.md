---
name: learn-user-habits
description: 学习用户习惯。用户要求“学习用户习惯”、周期性总结用户画像/工作习惯/协作灵魂/沟通规范/沟通偏好，或在对话中明确指导 agent 的沟通、验收、工作流、偏好、昵称、长期约束时使用；用于更新当前 author 私人软上下文，并把来源写回当前任务记录。
---

# 学习用户习惯

本 skill 用于把用户的明确指导和长期稳定观察沉淀到当前 author 的私人软上下文。它只更新当前 author 自己的用户区，不跨 author 写入。

## 使用流程

1. 从 `workstate.json.author` 确认当前 author；只写 `项目大脑/用户/{author}/`。
2. 读取当前 author 的 `用户画像.md`、`工作习惯.md`、`协作灵魂.md`、`沟通规范.md`、`沟通偏好.md`、`职责范围.md` 和当前任务 `process.md`。
3. 读取 `references/learning-workflow.md`，判断本次是“明确指导”还是“周期性总结”。
4. 读取 `references/private-context-targets.md`，决定写入哪个私人软上下文文件。
5. 有稳定结论才写入；不确定、一次性、任务内状态或证据不足的内容写回当前任务 `process.md` 作为候选观察。
6. 更新后在当前任务 `process.md` 记录来源、写入位置和变更摘要；形成稳定设计决策时写入 `design.md`。

## 参考文档表

| 主题 | 文件 | 内容 |
| --- | --- | --- |
| 学习流程 | `references/learning-workflow.md` | 明确指导、周期性总结、证据等级、写入步骤和安全边界 |
| 写入目标 | `references/private-context-targets.md` | `用户画像.md`、`工作习惯.md`、`协作灵魂.md`、`沟通规范.md`、`沟通偏好.md` 的分工和格式 |

## 示例表

| 场景 | 文件 | 内容 |
| --- | --- | --- |
| 用户明确指导 | `examples/explicit-user-guidance.md` | 用户直接告诉 agent 后续偏好时如何判断、写入和回写过程 |
| 周期性总结 | `examples/periodic-summary.md` | 根据一段任务记录提炼长期习惯，避免把临时任务状态写进软上下文 |
