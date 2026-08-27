# 发布 VUV 0.5.5 硬表面分组优化版 设计决策

开始日期: 2026-08-26

本文件只记录本任务形成的稳定设计决策。过程、尝试、失败路径和验收流水写入 `process.md`。

## 决策记录

- 以现有仓库目录契约发布：源码进入 `addons/visibility_uv_optimizer/`，安装包进入 `release/`，用户说明和验证证据进入 `docs/`。
- 0.5.5 覆盖分支中的可编辑插件源码，但保留 0.5.2、0.5.4 ZIP 和 smoke 作为历史版本；不把旧源码复制成第二套并行目录。
- Git 仓库只承载轻量交付物，不上传 177 MB 验证 Blend、`.blend1` 备份或失败的全资产结果。
- 本次只更新独立功能分支 `uv展开`，不合并或推送 `main`。
- 小岛只有在面数、Mesh 面积比和 UV 面积比三项同时满足时才作为碎片候选；避免把少面但占用大 UV 面积的有效主体误分类。
- 碎片 owner 按共享拓扑、模型空间距离、材质兼容的固定顺序选择；owner 与附属碎片作为一个原子 cell 排布，不因模型空间邻近而强行焊接 UV。
- 重复、镜像和旋转结构的 owner cohort 使用共同有向 UV 朝向并强制紧邻；无法同时满足紧邻、间距和零重叠时硬失败并回滚。
- 同一 RepeatGroup 跨多个 LayoutGroup 时，相关布局块固定使用相同 quarter-turn parity；优先保证最终方向一致，再考虑少量装箱率收益。
- 同一 RepeatGroup 跨多个 LayoutGroup 时，相关布局块先组成 affinity component 紧邻排布；亲和搜索预算按整个组件共享，耗尽时从候选循环立即逐层退出，避免复杂 owner 网络长时间占用 Blender 主线程。
- 场景证据使用 v10 `.blend`、manifest、SVG 和独立 audit 留在 `release/VUV_0.5.5_FinalScene_v10`，仓库只记录轻量验证摘要与哈希。
