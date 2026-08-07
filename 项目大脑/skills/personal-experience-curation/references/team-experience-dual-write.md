# 会议经验双落盘（个人 + 团队）

> 用途: **会议整理**场景下，经验认知须同时写入当前 author 私人 `经验/` 与公共 `项目大脑/团队经验/`。
> 触发: 用户明确要求会议经验入库，或 author 私人会议整理流程编排会议蒸馏时。

## 硬规则

1. **双落盘**：会议转写中经剪裁、对账后**确定落盘**的每条经验（新建或强化），须写入**两处**：
   - 个人：`项目大脑/用户/{author}/经验/`
   - 团队：`项目大脑/团队经验/`
2. **格式分轨**：
   - **团队经验**：**必须严格**按本 skill 的 `entry-template.md`（frontmatter、正文表字段、命名、INDEX 维护步骤）。禁止把私人区文件原样复制粘贴到团队区。
   - **个人经验**：允许按当前 author 私人 skill / 习惯的模板与写法落盘（如 team-lead 走 `private-experience-cognition/references/entry-template.md`）。
3. **格式不同时分开写**：若私人模板与公共 `entry-template.md` 在结构、字段或表述习惯上不一致，**各写各的文件**——语义对齐（同一 ID、同一认知），物理文件分别格式化；不得因「内容一样」而只写一侧或单侧 symlink。
4. **强化同步**：会议仅**强化**旧条时，个人与团队**同 ID** 条目均须更新（`何时用到`、`边界`、`updated` 等）；团队侧缺副本则按公共模板补建。
5. **原文归档**：会议原文进 `references/articles/` 时，个人与团队 `references/` **各归档一份**（或团队 references 指向同路径时须在两边 `references/INDEX.md` 各登记 `ref_id`）。

## 路径对照

| 产出 | 个人路径 | 团队路径 |
| --- | --- | --- |
| 条文正文 | `项目大脑/用户/{author}/经验/entries/{id}_{slug}.md` | `项目大脑/团队经验/entries/{id}_{slug}.md` |
| 分类索引 | `项目大脑/用户/{author}/经验/INDEX.md` | `项目大脑/团队经验/INDEX.md` |
| 外部原文 | `…/经验/references/articles/` | `项目大脑/团队经验/references/articles/` |
| 外部目录 | `…/经验/references/INDEX.md` | `项目大脑/团队经验/references/INDEX.md` |
| 团队入口说明 | — | `项目大脑/团队经验/index.md`（只读指引，不写条文） |

## 执行顺序（会议场景）

```
1. 剪裁 + 对账（personal-experience-curation / 私人等价 skill）
2. 写/强化 个人经验/entries + 更新个人 INDEX
3. 按 entry-template.md 单独写/强化 团队经验/entries + 更新团队 INDEX
4. 双写 references（若本轮有原文归档）
5. process.md 留痕：条目 ID、动作、个人/团队是否均已落盘
```

## 验证清单

- [ ] 每条落盘 ID 在个人 `entries/` 与团队 `entries/` 均存在（强化）或均已新建
- [ ] 团队侧条文通过 `entry-template.md` 字段检查（frontmatter + 表字段齐全）
- [ ] 未把私人格式文件未经转换复制到团队区
- [ ] 个人侧允许与团队侧正文表述不同，但 ID 与认知不矛盾
- [ ] 两边 INDEX 与整理记录（若有）已同步

## 与私人 skill 关系

| author | 个人格式依据 | 团队格式依据 |
| --- | --- | --- |
| 全员 | 各自私人 `经验/` 习惯或私人 experience skill 模板 | **固定**本文件 + `entry-template.md` |
| `team-lead` | `private-experience-cognition/references/entry-template.md` | **固定** `personal-experience-curation/references/entry-template.md` |

私人 skill **不覆盖**团队侧格式要求；会议场景下团队侧始终以公共模板为准。
