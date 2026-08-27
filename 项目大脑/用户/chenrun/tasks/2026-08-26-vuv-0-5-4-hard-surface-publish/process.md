# 发布 VUV 0.5.5 硬表面分组优化版

开始日期: 2026-08-26

## 用户原始需求

打包并上传 Visibility Aware UV Optimizer 硬表面试验版到 GitLab 的 `uv展开`
分支；后续实测要求重复/镜像/旋转机械结构同向紧邻，小碎片跟随模型空间附近主体。

## 目标

将最终版本更新为 0.5.5，完成重复结构 owner cohort 排布、双面积小岛判定、拼接武器
Blender 5.2 实测和跨版本回归，再提交并推送到 `origin/uv展开`。

## 当前状态

- 任务目录已创建。
- 已确认 author 为 `chenrun`，目标为远端 `uv展开` 分支。
- 已在独立克隆目录中工作，原始本地分析工作区不参与提交。
- 团队经验、个人经验和团队公理索引均为空，本任务无可引用条目。
- 后续工作从本文件继续追加过程和验收摘要。
- 稳定设计决策写入 `design.md`。
- v10 场景已由全新 Blender 5.2 后台进程独立审计，151/151 项通过。
- 0.5.5 最终源码已在 Blender 3.3.5 与 5.2.0 通过分组布局、Unique 局部修复和严格 smoke。
- 15 文件安装 ZIP 已重建并逐文件核对；功能提交 `b8d009f` 已推送到 `origin/uv展开`。

## 团队经验引用

- [x] 已读 `项目大脑/团队经验/INDEX.md`，按本任务 request/goal 点读 1–3 篇 `entries/`
- 相关条文 ID：暂无
- 与本任务关联：团队经验库当前无 entries。

## 个人经验引用

- [x] 已读 `项目大脑/用户/chenrun/经验/INDEX.md`，按本任务 request/goal 点读 0–3 篇 `entries/`
- 相关条文 ID：暂无
- 与本任务关联：个人经验库尚未建立 entries。

## 团队公理引用

- [x] 已读 `项目大脑/公理/index.md`「任务开始路由」，按 request/goal 点读 0–2 篇 `entries/`
- 推荐公理 ID：暂无
- 与本任务关联：团队公理库当前无 entries。

## 发布范围

- 更新 `addons/visibility_uv_optimizer/` 为通过 Blender 3.3.5 与 5.2.0 三组回归的 0.5.5 源码。
- 新增 0.5.5 安装 ZIP、smoke 和验证记录；保留 0.5.2、0.5.4 历史发布物。
- 不上传 Blender `.blend/.blend1`、失败的 RealAssets 结果、临时测试目录或用户现场文件。
- 只提交并推送远端 `uv展开` 分支，`main` 不变。

## 预提交验收

| 检查 | 结果 | 覆盖范围 | 未覆盖风险 |
| --- | --- | --- | --- |
| v10 拼接武器独立 postflight | 151/151，`passed=true`，0 failure | 3 个目标 Mesh 的拓扑/权重/旧 UV/新 UV 与 owner 语义 | 原场景已有缺失骨骼父级警告，不影响契约哈希 |
| 方向、重复与小岛语义 | Body 方向 `0 failed / 90 evaluated / 100 total`、owner `0/91`、小岛 `0/544`；Gun Head `0/5 + 0/13 + 0/86` | repeat/owner 共同方向、跨组亲和排布、cohort owner 紧邻与小岛 owner-cell 归属 | 仍需美术对最终排版做主观检查 |
| ZIP 与分支 `addons/` 逐文件 SHA256 | 15/15 一致；0 缓存条目 | 安装包与可编辑源码一致性 | 无 |
| `python -m py_compile` | 16/16 通过 | 13 个插件模块和 3 个仓库测试脚本 | 不代替 Blender 运行时 |
| Blender 3.3.5 / 5.2.0 分组布局回归 | 两版均 `VUV_GROUP_LAYOUT_REGRESSION_OK` | 13/17-owner 图、owner 共同朝向、跨组 quarter-turn 与亲和紧邻、8 个无关组隔离、组件共享预算、逆序确定性、双面积判定 | 无 |
| Blender 3.3.5 / 5.2.0 Unique 局部修复 | 两版均 `VUV_UNIQUE_LOCAL_REPAIR_OK` | 局部修复、Refine Layout、回滚 | 无 |
| Blender 3.3.5 / 5.2.0 strict smoke | 两版均 `VUV_055_SMOKE_OK` | 合同、Operator RNA、结构 owner 优先级和失败注入 | 真实资产覆盖以拼接武器为主 |
| 0.5.5 ZIP SHA-256 | `0B32EA15BC2E629DD607A11A3A522C33A440F8F74F44F029EE8175BF6F5AD712` | 正式安装包 | 无 |
| `release/SHA256SUMS.txt` | 7/7 匹配 | 0.5.2/0.5.4 历史包、0.5.5 ZIP 与四份 smoke | 无 |
| Markdown 本地链接 | 7/7 存在 | 根 README、新旧说明和校验清单 | GitLab 渲染待推送后复核 |
| `git diff --check` | 通过 | 空白、冲突标记和补丁格式 | 无 |
| 发布范围审查 | 功能提交 33 个文件；Blend、现场 manifest/SVG/audit、MCP/诊断脚本、缓存和临时目录均为 0 | 插件、测试、文档、release 和 chenrun 当前任务 | 验证记录保留现场绝对路径用于定位工作区证据 |
| 项目大脑 healthcheck | 本次任务无新增错误；全仓仍为 12 个既有错误 | 当前任务四件套、私人代码地图和索引 | 缺少适配入口、hook、公共索引覆盖等历史债务不在本任务范围 |
| 远端目标分支同步 | 提交前 fetch 为 `0/0`；功能提交 `b8d009f` 已推送 | 防止覆盖 `origin/uv展开` 新提交 | 状态回写提交后再次 fetch 复核 |

任务已完成；用户确认的 0.5.5 功能提交 `b8d009f` 已推送到 `uv展开`。状态回写提交
推送后再次 fetch，并以远端 HEAD 与本地 HEAD 一致作为最终结束条件。
