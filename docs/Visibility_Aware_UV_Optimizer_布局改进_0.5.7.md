# Visibility Aware UV Optimizer 0.5.7 布局改进记录

## 目标

解决拆分后的 UV 岛散乱、空白区域过多和长矩形横向摆放的问题，同时不改变 Mesh 拓扑、Seam、face-set 分区、UV 正面积重叠和失败回滚合同。

## 实现

- 明显长矩形按 UV AABB 宽高比识别，默认阈值为 `1.35`；主对称轴使用 modulo-180 方式吸附到 UV `V` 方向。
- 近似正方形不强制旋转；有符号 Geometry 方向、方向重复件和连续结构仍由原合同优先，避免破坏 checker/arrow 朝向。
- 顶层自由矩形块同步锁定竖向，防止后续 shelf Pack 把已校正的图表转回横向。
- 自由旋转的小批量排布增加确定性顺序候选，候选评分继续以最长边和统一缩放为硬约束，再降低外接矩形空洞。
- 分析快照新增 `long_rectangle_presentation` 和 `long_rectangle_min_aspect`，便于复现具体布局策略。

## 验证记录

| 项目 | Blender 3.3.5 | Blender 5.2.0 LTS |
|---|---|---|
| 全量 `test_*.py` | 通过 | 通过 |
| 长矩形方向/排布回归 | 通过 | 通过 |
| 严格 smoke | `VUV_055_SMOKE_OK` | `VUV_055_SMOKE_OK` |
| ZIP 导入链 | 通过 | 通过 |

## 后续方向

1. 在真实长条、薄板、bevel 密集和镜像件夹具上采集 `tile_polygon_coverage`、`aabb_fill` 和统一缩放基线。
2. 将布局评分和方向门禁纳入 CI，固定候选排序、ZIP 文件白名单与 hash 输出。
3. 对复杂 Unique 资产提供 dry-run 诊断和对象级失败原因，继续保持失败完整回滚。
