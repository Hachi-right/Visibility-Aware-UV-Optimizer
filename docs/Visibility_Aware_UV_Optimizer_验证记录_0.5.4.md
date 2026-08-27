# Visibility Aware UV Optimizer 0.5.4 验证记录

验证日期：`2026-08-26`

## 发布信息

- 插件版本：`0.5.4`
- 安装包：`release/Visibility_Aware_UV_Optimizer_0.5.4_HardSurface.zip`
- 安装包大小：`70020` 字节
- SHA-256：`E33D18EEE1F9A8460084F0AC6AB34111196715635BC3EEE3F384B7C94EA64661`
- ZIP 顶层：`visibility_uv_optimizer/`
- 发布文件：13 个，无 `__pycache__` 或测试临时文件

## 验证环境

- Windows
- Blender `3.3.5`
- Blender `5.2.0 LTS`
- 插件最低版本声明：Blender `3.3`

## 打包后 smoke

| 环境 | 结果 | 原始记录 |
| --- | --- | --- |
| Blender 3.3.5 | `VUV_054_SMOKE_OK` | `release/validation/VUV_0.5.4_HardSurface_Smoke_Blender3.3.json` |
| Blender 5.2.0 LTS | `VUV_054_SMOKE_OK` | `release/validation/VUV_0.5.4_HardSurface_Smoke_Blender5.2.json` |

覆盖内容包括：

- Marked Seam、材质边界和可选 Sharp 切线；
- 规则封闭圆柱和长导轨；
- 空 UV、隐藏几何、多 UV 层与 active/render 身份；
- Trim Sheet 等非 Unique 布局保护；
- Blender 3.3/5.2 operator 参数差异和 UV 全选 Pack；
- Panel Flatness、LED1/VFX 自动合同；
- overlap、winding、退化、越界注入失败和事务回滚。

## Blender 5.2 真实资产

`SM_CDO_ChopSword_1_LOD1` 通过 Unique 最终门禁：

| 指标 | 结果 |
| --- | ---: |
| 面 | 2,683 |
| 三角形 | 4,246 |
| UV 岛 | 220 |
| 正 winding | 4,246 |
| 负 winding | 0 |
| UV 退化 | 0 |
| 正面积 overlap | 0 |
| UV bounds | `[0.002, 0.002, 0.95879948, 0.99800003]` |
| P95 Stretch | 1.50828855 |
| Max Stretch | 2.53489069 |

保存后使用 Blender 5.2 重新打开验证文件并只读复算，结果仍为 1/1 PASS。

另外四个抽样 Unique 资产仍包含无法修复的折叠或退化图表，插件拒绝提交并完整恢复
原状态。完整真实资产批次仍为 FAIL，因此 0.5.4 的定位是受控试验版，不是生产
全量自动展开已经完成。

## 发布边界

Git 仓库只包含插件源码、安装 ZIP、轻量 smoke 记录和文档。验证 Blend、`.blend1`
备份、用户现场文件和失败的 RealAssets 结果不进入仓库。
