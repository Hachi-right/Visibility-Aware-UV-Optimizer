# Visibility Aware UV Optimizer 代码地图

> author: `chenrun`
> 最后更新时间：2026-08-27
> 最后验证版本：VUV 0.5.5，Blender 3.3.5 / 5.2.0 LTS
> 相关任务：`2026-08-26-vuv-0-5-4-hard-surface-publish`

## 目录入口

- 可编辑源码：`addons/visibility_uv_optimizer/`
- Blender 安装包：`release/Visibility_Aware_UV_Optimizer_0.5.5_HardSurface.zip`
- 中文说明：`docs/Visibility_Aware_UV_Optimizer_硬表面使用说明_0.5.4.md`
- 验证记录：`docs/Visibility_Aware_UV_Optimizer_验证记录_0.5.5.md`
- 跨版本 smoke：`release/validation/`

## 运行入口

- `addons/visibility_uv_optimizer/__init__.py`：`bl_info`、`register`、`unregister` 和 Blender 类注册表。
- `addons/visibility_uv_optimizer/operators.py`：`VUV_OT_OptimizeUV` 是 UI 触发的 UV 优化 Operator；同文件还包含可见性分析和探针导入导出入口。
- `addons/visibility_uv_optimizer/properties.py`：`VUVSettings`、Initial UV Mode、UV Usage、硬表面参数和默认值。
- `addons/visibility_uv_optimizer/ui.py`：3D Viewport 的 `UV Optimizer` 面板布局。

## 优化主链

- `addons/visibility_uv_optimizer/uv_optimize.py`：`optimize_active_object` 是活动 Mesh 的优化主入口。
- `_resolve_uv_usage`：根据对象名和显式设置解析 Unique、Trim Sheet、Info Atlas、LED/VFX 合同。
- `_resolve_initial_uv_mode`：决定 Auto、Hard Surface、Marked Seams、Refine Layout、Preserve Layout 或 Legacy Smart。
- `_snapshot_mesh_uv_state` / `_restore_mesh_uv_state`：事务快照与失败回滚。
- `_audit_unique_layout`：Unique 的 finite、0-1、winding、退化、overlap 和 Seam 一致性门禁。
- `_select_all_uvs_for_operator` / `_call_uv_operator`：兼容 Blender 3.3/5.2 的 UV Operator 调用。
- `addons/visibility_uv_optimizer/small_island_cleanup.py`：只对同时满足 Mesh 与 UV 面积阈值的小岛尝试拓扑安全缝合。
- `addons/visibility_uv_optimizer/uv_group_layout.py`：分析重复/镜像/旋转结构，按拓扑、模型空间距离、材质为碎片选 owner，并将 owner cell 与 cohort 做共同朝向和确定性紧邻排布；跨 LayoutGroup repeat 使用一致 quarter-turn parity。

## 硬表面规则

- `addons/visibility_uv_optimizer/hard_surface.py`：平面、倒角带、圆柱侧壁、端盖和通用区域的几何分类与初始 UV 生成。
- `classify_edge_constraints`：汇总 Artist Seam、材质边界、可选 Sharp、圆柱结构边和非流形边约束。

## 查找经验

- 先从 `VUV_OT_OptimizeUV` 反向定位 `optimize_active_object`，再沿 `initial_uv_mode` 和 `uv_usage` 跟踪分支。
- 检查最终安全性时优先查看 `_snapshot_mesh_uv_state`、异常恢复路径和 `_audit_unique_layout`，不要只检查候选合并阶段的 `reject_overlap`。
- 排查“重复结构仍分散”时沿 `owner_cohorts`、`_pack_owner_cells`、`_affinity_cohorts_valid` 追踪；排查“小面归属错误”时先核对双面积 `AND` 判定，再看 owner 的拓扑和模型空间距离排序。
- 发布时同时核对源码目录、ZIP 解压目录和两版 smoke；ZIP 顶层必须只有 `visibility_uv_optimizer/`。
