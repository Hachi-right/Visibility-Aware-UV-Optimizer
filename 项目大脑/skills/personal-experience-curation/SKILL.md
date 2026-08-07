---
name: personal-experience-curation
description: 公共个人经验整理 skill。从 task、会议、工作记录提炼非共识反常点，反推为何有效，写入当前 author 经验/entries 并更新 INDEX。用户说整理经验、沉淀认知、经验剪裁、写经验条文时使用。
---

# personal-experience-curation

本 skill 定义 **当前 author 私人经验怎么写**。架构：`经验/INDEX.md` + `经验/entries/{id}_{slug}.md`。**按积累触发**，不做冷启动到期检查、专用 worktree 或后台锁。

## 与相关 skill 关系

| skill | 关系 |
| --- | --- |
| `personal-axioms` | 思考层公理（若当前 author 有）；可交叉引用，禁止大段重复 |
| **本 skill** | 被要求整理时：反常点、认知非行为、单篇结构、INDEX 维护 |

## 何时加载

| 触发 | 动作 |
| --- | --- |
| 用户要求整理/沉淀私人经验（写正文） | 读本 skill + `references/curation-rules.md` |
| 从 task 抽经验、写新条文 | 读 `references/entry-template.md` |
| 用户给外部文章/演讲/会议转写要求「抽取经验」 | 原文落盘 `经验/references/articles/` → 再写 `entries/` |
| 批量蒸馏、验证轮、反例判断 | 读 `references/distillation-methodology.md` |
| 用户要求把团队经验上升为团队规则 | **仅 `team-lead` / `tech-lead` 可执行**；读 `references/team-experience-to-axiom.md`（含 AQT）。其他 author 只提名，不改 `docs/规则/` |

## 工作流程

```
1. 扫 tasklist + 已完成 task 的 design.md（优先）/ process.md
2. 按 curation-rules 评分：反常点 7+ 才写；常识硬砍
3. 先出整理方案 → 当前 author 确认
   （仅 team-lead：若 A/B/C/D 明显装不下，按 curation-rules §4.1 停下问，不得自创顶层类）
4. 验证轮：反例狩猎 + 跨 task 证据 + 互斥性检查
5. 按 entry-template 写 entries/ + 更新 经验/INDEX.md
6. 按需更新 项目大脑/用户/{author}/index.md
```

## author 条件规则

| author | 额外硬规则 |
| --- | --- |
| `team-lead` | 读 `references/curation-rules.md` **§4.1**；某类明显装不下时必须停下来问，禁止自创 E/F 类或强行归类 |
| 其他 author | **不加载 §4.1**；类别按各自 INDEX 与私人 skill 维护 |

## 核心硬规则

全文见 `references/curation-rules.md`；分层与硬拒绝见 `references/curation-hard-rules.md`、`experience-evaluation.md`、`experience-curation-standards.md`。

**仅 `team-lead`**：整理前确认 `workstate.json.author`；命中则额外遵守 `curation-rules.md` §4.1（装不下必须问）。其他 author 忽略 §4.1。

## 参考文件

| 路径 | 用途 |
| --- | --- |
| `references/curation-rules.md` | 剪裁标准、类别表 |
| `references/entry-template.md` | 单篇模板 |
| `references/distillation-methodology.md` | 批量蒸馏方法论 |
| `references/curation-hard-rules.md` | 分层、求证文档、积累触发 |
| `references/experience-evaluation.md` | 评分表 |
| `references/experience-curation-standards.md` | 长期价值、L0–L3 |
| `references/team-experience-dual-write.md` | 会议场景：个人 + 团队双落盘（用户明确要求时） |
| `references/team-experience-to-axiom.md` | **上升路径**：团队经验 → `docs/规则/` 门槛、蒸馏、资料地图路由 |

## 产出位置

| 产出 | 路径 |
| --- | --- |
| 经验正文 | `项目大脑/用户/{author}/经验/entries/{id}_{slug}.md` |
| 分类索引 | `项目大脑/用户/{author}/经验/INDEX.md` |
| 整理确认 | `项目大脑/用户/{author}/经验/整理确认.md`（批量整理后） |
