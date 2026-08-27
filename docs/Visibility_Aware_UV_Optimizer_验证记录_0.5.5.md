# Visibility Aware UV Optimizer 0.5.5 验证记录

验证日期：`2026-08-27`

## 验证目标

本轮针对硬表面模型的两类可读性问题：

- 左右镜像、绕轴旋转、同构件和重复机械结构的 UV 不应分散或方向杂乱；
- 意义不明的小面不应独立散落，应随模型上邻近的主体结构排布。

0.5.5 将重复结构组织为 owner cohort。每个主体 owner 与归属于它的小碎片组成
不可拆分的原子 cell；同一 cohort 的 owner cells 采用共同的有向 UV 朝向并紧邻
排布。布局必须同时满足配置间距、零正面积重叠和 cohort 紧邻约束；确定性候选
搜索、有限回溯与渐进扩展均失败时，本次操作硬失败并完整回滚。

零碎小面的 owner 选择顺序固定为：共享 Mesh 拓扑优先、模型空间距离次之、材质
一致性再次。归属只改变布局分组；没有合法共享拓扑边时不会把两个 UV 岛焊接。

## 小岛判定修正

小岛布局候选必须同时满足：

```text
face_count <= 12
mesh_area_ratio <= 0.001
uv_area_ratio <= 0.001
```

面积条件固定为 `Mesh AND UV`，两个面积阈值缺一不可。这项修正避免把“面数很少、
3D 面积很小，但占用较大 UV 画布”的有效图表误当作附属碎片。`Body` 中
图表 `534` 是对应回归样本：它只有 2 个面且 3D 面积很小，但约占总 UV 面积的
63%，新规则不再将它归入小岛。

## 验证环境

- Windows
- Blender `3.3.5`
- Blender `5.2.0 LTS`
- 插件最低版本声明：Blender `3.3`
- 生成 v10 场景时 Blender 5.2 已加载插件的 15 文件指纹：
  `c9ad32c4f3388ed3ad6c81d67cbcdb0960afd5f26188488803210cb61b259f53`
- 最终发布包 15 文件指纹：
  `c9ad32c4f3388ed3ad6c81d67cbcdb0960afd5f26188488803210cb61b259f53`

场景实测与最终发布使用相同的 15 文件构建。两个面积阈值的 UI tooltip 与实现均为
`Mesh AND UV`；最终方向计算同时纳入 repeat group 和 owner cohort。跨 LayoutGroup
的同一 RepeatGroup 在最终 shelf pack 中保持相同 quarter-turn parity，避免一横一竖。
跨布局组的重复件还会先组成 affinity component，防止无关布局块插入其间。亲和组件
搜索使用整个组件共享的确定性状态预算，避免预算随 owner 数量成倍增长；预算耗尽
会从候选循环立即逐层退出，不再继续执行无效的局部校验。

关键布局源码哈希：

| 文件 | SHA-256 |
| --- | --- |
| `uv_group_layout.py` | `af3d8bc00fd3443459f660101fb63e11c98ba852c2df4f8fe87591420a9246fa` |
| `small_island_cleanup.py` | `c390fa543325ada33969cf14e2b53932ec8f7913883937a3479058e05e0536e9` |

## Blender 3.3 / 5.2 回归

| 验证项 | Blender 3.3.5 | Blender 5.2.0 LTS |
| --- | --- | --- |
| 分组布局回归 | `VUV_GROUP_LAYOUT_REGRESSION_OK` | `VUV_GROUP_LAYOUT_REGRESSION_OK` |
| Unique 局部修复 | `VUV_UNIQUE_LOCAL_REPAIR_OK` | `VUV_UNIQUE_LOCAL_REPAIR_OK` |
| 0.5.5 严格 smoke | `VUV_055_SMOKE_OK` | `VUV_055_SMOKE_OK` |

分组布局回归同时覆盖原始 13-owner `Body` 关系图、新 `AND` 小岛判定后的 17-owner
关系图、owner cohort 共同朝向、跨布局组 quarter-turn parity、8 个无关布局组隔离场景、
组件级搜索预算，以及逆序输入的确定性结果。smoke 原始记录：

- `release/validation/VUV_0.5.5_HardSurface_Smoke_Blender3.3.json`
- `release/validation/VUV_0.5.5_HardSurface_Smoke_Blender5.2.json`

两版 smoke 均覆盖重复小碎片的 owner 优先级、结构分组、Unique 失败注入和事务
回滚；测试状态为严格 0.5.5 模式。

## Blender 5.2 场景实测

场景：`拼接武器`

输出文件：

- `C:\Users\SEASUN\Documents\ChatGPT\绑骨\release\VUV_0.5.5_FinalScene_v10\拼接武器_VUV_分组优化_v10.blend`
- `C:\Users\SEASUN\Documents\ChatGPT\绑骨\release\VUV_0.5.5_FinalScene_v10\拼接武器_VUV_分组优化_v10_manifest.json`
- `C:\Users\SEASUN\Documents\ChatGPT\绑骨\release\VUV_0.5.5_FinalScene_v10\拼接武器_VUV_分组优化_v10_对比.svg`
- `C:\Users\SEASUN\Documents\ChatGPT\绑骨\release\VUV_0.5.5_FinalScene_v10\拼接武器_VUV_分组优化_v10_audit.json`

输出 Blend SHA-256：
`ba0717b93e0724c878f73037f7566fc033a52a15008be31e31c41f38d2c55c28`

源文件未覆盖；结果写入 `VUV_Grouped_Final`，并保留原 `VUV_Unique_Final` 供比较。

| Mesh | UV 岛变化 | 安全缝合 | 重复组 / 成员 | 归属主体的小岛 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `Bullet` | `4 -> 4` | `0` | `0 / 0` | `0` | 有限坐标，位于 0-1 |
| `Body` | `681 -> 680` | `1` | `14 / 53` | `544` | 有限坐标，位于 0-1 |
| `Gun Head` | `209 -> 203` | `6` | `3 / 10` | `86` | 有限坐标，位于 0-1 |

`Body` 的最终 680 岛反映新双面积判定：图表 `534` 保持为有效主体，不再作为小碎片
跟随其他 owner。`Gun Head` 的 6 次减少来自通过拓扑、拉伸、winding、退化和 overlap
检查的安全缝合，不是模型空间邻近导致的强制焊接。

在一个全新的 Blender 5.2 后台进程中加载 v10 后，独立 postflight 完成 `151` 项
检查：顶层 `passed=true`、`failures=[]`。审计文件为
`C:\Users\SEASUN\Documents\ChatGPT\绑骨\release\VUV_0.5.5_FinalScene_v10\拼接武器_VUV_分组优化_v10_audit.json`，SHA-256 为
`f124381ec92d1aa2e383f5eb88270101859edf5050d2b06c36ce96da4fbb21f8`。

| Mesh | 三角形 / 正 winding | overlap / 负 winding / 源退化 / UV 退化 | 方向失败 / 已评估 / 总数 | 重复组失败 / 已评估 / 总数 | 重复 owner 检查失败 | 小岛归属检查失败 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `Bullet` | `80 / 80` | `0 / 0 / 0 / 0` | `0 / 0 / 0` | `0 / 0 / 0` | `0 / 0` | `0 / 0` |
| `Body` | `5169 / 5169` | `0 / 0 / 0 / 0` | `0 / 90 / 100` | `0 / 81 / 91` | `0 / 91` | `0 / 544` |
| `Gun Head` | `3216 / 3216` | `0 / 0 / 0 / 0` | `0 / 5 / 5` | `0 / 5 / 5` | `0 / 13` | `0 / 86` |

表中的“失败 / 总数”直接来自独立重建的最终 UV 岛、方向分量与 owner-cell AABB，
不复用插件自己的通过标记。方向分量由 repeat group 与 owner cohort 传递合并；全员
具有可靠方向锚点时按 360 度检查，否则按主轴 180 度等价检查。任一方向、紧邻、
owner 归属或零重叠条件失败，都会使整次审计失败。

## MCP 操作注意事项

Blender MCP bridge 在本轮长事务中偶尔返回前一次调用的结果，同时后台实际执行
当前请求。不要据单次返回文本重复提交同一长事务，否则可能造成重复展开或覆盖
验证上下文。推荐流程：

1. 提交一次长事务后等待 Blender 完成；
2. 用轻量只读报告调用排空延迟返回；
3. 直接核对输出 `.blend`、manifest、时间戳和 SHA-256；
4. 使用新的 Blender 5.2 后台进程执行独立 postflight，不复用 MCP 会话状态。

## 发布边界

v10 是使用最终双面积小岛判定、owner 共同朝向、跨布局组旋转锁、亲和组件和搜索
预算立即终止保护生成的发布验证结果。v2 保留为失败对比，v3-v9 是中间结果，均不能
替代 v10。仓库只收录
插件源码、安装 ZIP、轻量验证记录与文档；现场 Blend、manifest、对比 SVG、audit
和 Blender 自动备份不进入仓库。
