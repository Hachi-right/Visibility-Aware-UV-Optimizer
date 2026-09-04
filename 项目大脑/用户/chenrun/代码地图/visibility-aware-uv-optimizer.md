# Visibility Aware UV Optimizer 代码地图

> author: `chenrun`
> 最后更新时间：2026-09-04
> 最后验证版本：VUV 0.5.10，Blender 3.3.5 / 5.2.0 LTS
> 相关任务：`2026-08-31-vuv-uv-direction-consistency`

## 目录入口

- 可编辑源码：`addons/visibility_uv_optimizer/`
- Blender 安装包：`release/Visibility_Aware_UV_Optimizer_0.5.10_HardSurface.zip`
- 中文说明：`docs/Visibility_Aware_UV_Optimizer_硬表面使用说明_0.5.4.md`
- 方向说明：`docs/Visibility_Aware_UV_Optimizer_方向一致性_0.5.6.md`
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
- `_align_hard_surface_chart`：按 Object/World 与固定轴优先级执行有符号方向对齐，并在
  无分组路径记录 Pack 前 face-set/选轴合同。
- `_audit_directed_object_mesh`：关闭结构分组时仍执行独立最终方向门禁。
- `addons/visibility_uv_optimizer/small_island_cleanup.py`：只对同时满足 Mesh 与 UV 面积阈值的小岛尝试拓扑安全缝合。
- `addons/visibility_uv_optimizer/uv_group_layout.py`：分析重复/镜像/旋转结构，按拓扑、模型空间距离、材质为碎片选 owner，并将 owner cell 与 cohort 做共同朝向和确定性紧邻排布；跨 LayoutGroup repeat 使用一致 quarter-turn parity。
- `_geometry_direction_record` / `_directed_geometry_metrics`：从 Geometry Jacobian 解析
  模型正轴到 UV `+V` 的有符号残差并执行最终方向评分。
- `rebind_geometry_axis_contract`：严格匹配全部 face-set，并按 Pack 前原选轴重算，禁止
  `AUTO` 换轴或已解析轴丢失。

## 硬表面规则

- `addons/visibility_uv_optimizer/hard_surface.py`：平面、倒角带、圆柱侧壁、端盖和通用区域的几何分类与初始 UV 生成。
- `classify_edge_constraints`：汇总 Artist Seam、材质边界、可选 Sharp、圆柱结构边和非流形边约束。
- `GeometryAlignmentResult` / `align_island_geometry_report` /
  `align_island_geometry`：面积加权 UV Jacobian、有符号模型轴选择和刚性 UV 对齐。

## 方向设置与测试入口

- `properties.py`：`hard_surface_direction_lock`、`uv_direction_space`、
  `uv_direction_axis`。
- `tests/test_uv_direction_geometry.py`：Geometry 方向数学与尺度不变性。
- `tests/test_uv_direction_pipeline.py`：完整优化、分组开关、原选轴保持与回滚。
- `tests/test_uv_group_layout_regressions.py`：结构布局、Adaptive 回放和 face-set 合同。
- `tests/test_unique_uv_repair_fallback.py`：局部修复后重定向与事务恢复。

## 查找经验

- 先从 `VUV_OT_OptimizeUV` 反向定位 `optimize_active_object`，再沿 `initial_uv_mode` 和 `uv_usage` 跟踪分支。
- 检查最终安全性时优先查看 `_snapshot_mesh_uv_state`、异常恢复路径和 `_audit_unique_layout`，不要只检查候选合并阶段的 `reject_overlap`。
- 排查棋盘格方向时沿“初始方向对齐 -> 局部修复 -> 原生 Pack -> 结构/Adaptive
  回放 -> 最终方向审计”反向追踪；不能只看首次 Unwrap 的角度。
- 排查“重复结构仍分散”时沿 `owner_cohorts`、`_pack_owner_cells`、`_affinity_cohorts_valid` 追踪；排查“小面归属错误”时先核对双面积 `AND` 判定，再看 owner 的拓扑和模型空间距离排序。
- 发布时同时核对源码目录、ZIP 解压目录和两版 smoke；ZIP 顶层必须只有
  `visibility_uv_optimizer/`，精确 15 个白名单文件，并逐文件比对哈希。
