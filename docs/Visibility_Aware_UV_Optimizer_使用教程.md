# Visibility Aware UV Optimizer 使用教程

本文对应插件版本 `0.5.2`。

## 1. 插件用途

插件分为四类能力：

1. 从相机、包围球或外部空气连通性分析模型表面可见程度。
2. 用绿、黄、红热力图检查哪些面需要保留纹理面积。
3. 在可控拉伸、翻转、重叠和拓扑约束下减少 UV 岛碎片。
4. 将完全不需要纹理覆盖的红面统一收拢到 UV 原点 `(0, 0)`。

它适合机械结构、载具、角色装备、内外壳、遮挡件等需要按最终观察范围分配
UV 空间的资产。

## 2. 版本与兼容性

- 当前版本：`0.5.2`
- 最低 Blender 版本：`3.3`
- 已完成运行回归：Blender `3.3.5`
- 源码兼容 Blender 3.3 至 5.x 的面板和热力图接口
- 本次未使用 Blender 5.1 可执行程序做运行时回归

## 3. 安装

### 3.1 首次安装

1. 下载 `release/Visibility_Aware_UV_Optimizer_0.5.2.zip`。
2. 打开 Blender。
3. 进入 `Edit > Preferences > Add-ons`。
4. 点击 `Install`。
5. 选择下载的 ZIP，不要提前解压。
6. 搜索 `Visibility Aware UV Optimizer`。
7. 勾选插件左侧复选框。
8. 回到 3D 视图，按 `N` 打开侧栏。
9. 打开 `UV Optimizer` 标签页。

### 3.2 从旧版升级

1. 在 `Preferences > Add-ons` 中找到旧版插件。
2. 取消勾选旧版。
3. 安装 `0.5.2` ZIP。
4. 重新启用插件。
5. 建议重启 Blender，清除旧面板缓存。

若面板中仍显示 `Hidden UV Scale` 或 `Hidden Corner Size`，说明 Blender
仍在使用旧模块。重启 Blender；仍未消失时，删除旧插件目录后重新安装。

### 3.3 面板位置

`3D Viewport > Sidebar(N) > UV Optimizer`

面板分为：

- `Cameras`
- `Visibility`
- `Face Override`
- `UV Optimization`
- `Texture Direction Probe`
- `Interactive Mapping`

## 4. 操作前准备

1. 保存 `.blend` 文件，或复制一份模型。
2. 确认模型不是空网格，并且面法线基本正确。
3. 建议先应用会明显改变拓扑的修改器。
4. 如果多个对象共用同一份 Mesh Data，先执行 `Object > Relations >
   Make Single User > Object & Data`。
5. 可见性分析可以同时选择多个网格，用于计算对象之间的联合遮挡。
6. UV 优化只处理活动网格对象，也就是带黄色轮廓的对象。
7. 优化会重写活动 UV Map 和接缝标记，应保留可回退版本。

没有现成 UV 也可以运行优化；插件会先建立 Smart UV 初始结果。

## 5. 最短使用流程

1. 选择参与可见性分析的网格。
2. 在 `Visibility > Ray Source` 中选择分析方式。
3. 点击 `Analyze Visibility`。
4. 点击 `Heatmap` 检查颜色。
5. 必要时进入编辑模式，选择面，用 `Important / Auto / Hidden` 修正。
6. 回到对象模式，将目标模型设为活动对象。
7. 保持默认 UV 参数，点击 `Optimize Active Object UV`。
8. 打开 UV Editor 检查结果。
9. 红面应全部叠在左下角 UV 原点 `(0, 0)`。
10. 检查绿色重要区域是否有不合理接缝、翻转或明显拉伸。

## 6. 可见性颜色含义

| 颜色 | 含义 | UV 处理 |
| --- | --- | --- |
| Green | 高优先级可见面 | 保持正常或更高纹理密度 |
| Yellow | 低可见度但仍需要纹理的面 | 保留 UV，降低纹理密度 |
| Red | 可见性为零或手动标记 Hidden | 全部收拢到 `(0, 0)` |

`High Threshold` 默认值为 `0.05`：

- 分数大于等于阈值：绿色。
- 分数大于 0 且低于阈值：黄色。
- 分数等于 0：红色。

提高阈值会让更多低分面变成黄色，不会直接把大于 0 的面变成红色。

## 7. 可见性分析方式

### 7.1 Cameras

适合有明确最终视角、六视图检查或镜头范围固定的资产。

`Camera Source`：

- `Auto 6`：自动创建正负 X、Y、Z 六个正交相机。
- `Selected`：使用当前选中的相机。
- `Hybrid`：同时使用自动相机和选中相机。

`Axis Basis`：

- `Active Object`：六相机沿活动对象局部轴布置。
- `World`：沿世界坐标轴布置。

常用参数：

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| Frame Margin | 0.08 | 自动相机包围模型时的画面余量 |
| Sampling | 128 Standard | 每个相机的射线采样密度 |
| Ignore Backfaces | 开启 | 不把背向射线的面当作可见面 |

推荐步骤：

1. 选择网格。
2. `Camera Source` 选择 `Auto 6`。
3. 点击 `Create / Update`。
4. `Sampling` 先用 `64 Fast` 或 `128 Standard`。
5. 点击 `Analyze Visibility`。

### 7.2 Sphere

适合需要从物体周围均匀观察的资产，比固定六视图更全面。

插件会在选择对象外建立一个球形采样器，并从球面向中心发射对称射线。

| 参数 | 默认值 | 建议 |
| --- | ---: | --- |
| Sphere Samples | 256 | 常规资产先用 256 |
| Coverage Grid | 3 x 3 | 小面或掠射表面可用 5 x 5 |
| Sphere Radius | 1.15 | 一般保持默认 |
| Show Sphere | 开启 | 仅控制辅助球显示 |
| Hit Layers | 3 | 同一射线记录多层表面 |
| Layer Falloff | 0.35 | 越深层权重越低 |
| Sphere Weight | 1.0 | Hybrid 模式中的球面权重 |

`Coverage Grid`：

- `1 x 1`：每个方向一条中心射线，最快。
- `3 x 3`：每个方向九条平行射线，推荐默认。
- `5 x 5`：每个方向二十五条射线，适合小面，速度较慢。

`Hit Layers` 可让内层或半遮挡表面获得较低但非零的分数，避免所有内层结构
直接变红。

### 7.3 Exterior Flood

适合判断表面是否和外部空气连通，例如：

- 封闭壳体内部。
- 开口管道、桶体、孔洞。
- 深凹槽和可从外部进入的腔体。

该模式建立体素表面，从包围盒边界向内部执行外部空气洪泛。

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| Exterior Resolution | 96 | 最大轴体素数 |
| Water Depth Falloff | 0.12 | 开放腔体越深，优先级越低 |
| Exterior Face Samples | 4 | 每个面的外部接触采样数 |

推荐先使用 `96`。小孔识别不足时提高到 `128` 或 `192`，但耗时和内存会增加。

注意：Exterior Flood 判断的是外部空气连通性，不等于某个镜头能直接看到。
开放桶体内部可能与外界连通，但会因深度获得较低优先级。

### 7.4 Camera + Sphere

适合既要遵循指定镜头，又不希望漏掉模型周围重要表面的情况。

1. 相机射线提供最终视角证据。
2. 球面射线补充周围覆盖。
3. `Sphere Weight` 控制球面证据相对权重。

可以先用默认权重 `1.0`，再根据热力图调整。

## 8. 热力图和显示过滤

执行 `Analyze Visibility` 后：

- `Heatmap`：显示或隐藏彩色覆盖。
- `Opacity`：控制热力图透明度。
- `Hidden`：选中红面。
- `Low`：选中黄面。

`Face Display` 下的 `Green / Yellow / Red` 是非破坏性显示过滤：

- 关闭某一颜色只隐藏该类面，不删除面，不修改分数。
- `Show All` 恢复原始显示状态。
- 切换显示过滤不需要重新分析。

建议检查顺序：

1. 只显示红面，确认它们是否确实不需要纹理。
2. 只显示黄面，检查内部结构、小倒角和边缘过渡。
3. 恢复全部颜色，查看整体分类是否连贯。

## 9. 手动修正 Face Override

在编辑模式中选择面，再使用：

- `Important`：强制设为高优先级，显示为绿色。
- `Hidden`：强制设为完全隐藏，显示为红色。
- `Auto`：清除人工覆盖，恢复自动分析结果。

典型用法：

- 自动分析误判但最终镜头会看到的面：`Important`。
- 结构上外露但材质设计明确不需要的面：`Hidden`。
- 重新分析后希望恢复自动判断：`Auto`。

人工覆盖独立保存在 `vuv_override` 面属性中。自动结果保存在
`vuv_visibility_auto`，因此 `Auto` 可以恢复分析值，不会丢失原始结果。

## 10. UV 优化

### 10.1 优化会做什么

1. 对活动网格生成 Smart UV 初始拆分。
2. 根据相邻岛、可见性、表面连续性和接缝成本搜索合并候选。
3. 尝试展开合并区域。
4. 检查拓扑、拉伸、翻转和 UV 重叠。
5. 只接受通过检查的合并。
6. 按可见性和局部细节调整纹理密度。
7. 打包 UV 岛。
8. 把所有红面 UV 环固定写入 `(0, 0)`。
9. 根据最终 UV 更新接缝。

### 10.2 红面固定规则

版本 `0.5.2` 中红面没有可调节的大小和角落范围：

```text
所有红面 UV = (0, 0)
```

这等效于把红面 UV 全选后，在 UV 游标位于原点时执行
`Snap Selected to Cursor`，但插件直接写入坐标，不依赖当前编辑器、游标位置或
界面上下文。

因此：

- 红面之间会完全重叠。
- 红面不需要独立纹理覆盖。
- 绿色和黄色 UV 不会为红面预留右上角空间。
- 面板中不再有 `Hidden UV Scale` 和 `Hidden Corner Size`。

### 10.3 UV 参数说明

| 参数 | 默认值 | 作用 |
| --- | ---: | --- |
| Initial Angle | 55° | Smart UV 初始切分角度 |
| Merge Search Angle | 110° | 搜索相邻岛合并的最大角度范围 |
| Developable Bands | 开启 | 偏好连续平面、圆柱、圆锥和倒角带 |
| Developable Angle | 45° | 可展曲面相邻面角度范围 |
| Band Merge Bonus | 0.25 | 连续带减少碎片的偏好强度 |
| Seam Visibility Weight | 1.5 | 可见光滑区域保留接缝的代价 |
| Hard Edge Seam Bonus | 0.35 | 偏好把硬边保留为接缝 |
| Concave Seam Bonus | 0.25 | 偏好把凹边保留为接缝 |
| Detail Density Strength | 0.65 | 高法线变化区域增加纹理密度 |
| Detail Density Cap | 1.75 | 局部细节密度最大倍率 |
| P95 Stretch | 1.35 | 95 分位拉伸上限 |
| Max Stretch | 2.5 | 单个三角形最大拉伸上限 |
| Merge Tests | 250 | 最多尝试的合并次数 |
| Small Island Faces | 8 | 小岛面数判断阈值 |
| Small Island Area | 0.002 | 小岛面积比例判断阈值 |
| Preserve Existing Seams | 开启 | 保留原有接缝作为约束 |
| Respect Material Borders | 开启 | 不跨材质边界合并 |
| Respect Sharp Edges | 关闭 | 开启后不跨 Sharp 边合并 |
| Reject UV Overlap | 开启 | 拒绝产生非预期重叠的合并 |
| Island Margin | 0.002 | 最终 UV 岛间距 |

### 10.4 推荐调参顺序

先保持默认值，只在结果存在明确问题时调整：

1. 岛仍过碎：提高 `Merge Tests`，适度提高 `Merge Search Angle`。
2. 圆柱、倒角带被切碎：保持 `Developable Bands` 开启，适度提高
   `Band Merge Bonus`。
3. 合并后拉伸过大：降低 `P95 Stretch` 和 `Max Stretch`。
4. 可见光滑区域接缝太多：提高 `Seam Visibility Weight`。
5. 硬边被跨越：开启 `Respect Sharp Edges`，或提高
   `Hard Edge Seam Bonus`。
6. 不同材质被连在一起：保持 `Respect Material Borders` 开启。
7. UV 间距不足：提高 `Island Margin`。

不要一次同时修改多个参数，否则难以判断是哪一个设置造成变化。

## 11. 推荐预设

### 11.1 快速预览

- Ray Source：`Cameras`
- Camera Source：`Auto 6`
- Sampling：`64 Fast`
- UV 参数：全部默认

适合快速检查分类和基础 UV 结果。

### 11.2 常规资产

- Ray Source：`Sphere`
- Sphere Samples：`256`
- Coverage Grid：`3 x 3`
- Hit Layers：`3`
- UV 参数：全部默认

适合从周围观察的机械、道具和装备。

### 11.3 内外壳和深腔

- 先用 `Exterior Flood / 96` 判断外部空气连通性。
- 再用 `Camera + Sphere` 检查实际观察范围。
- 对设计明确不需要的内部面手动设为 `Hidden`。

## 12. Texture Direction Probe

方向探针是可选功能。它用于接收外部贴图或表面探测流程提供的边界证据，
辅助判断两个 UV 岛是否具有相同纹理方向、尺度和相位。

### 12.1 基本流程

1. 外部流程生成探针 JSON。
2. 选择与 JSON 对应且拓扑未变化的活动对象。
3. 点击 `Texture Direction Probe > Import`。
4. 开启 `Use Direction Probe`。
5. 设置 `Probe Confidence`。
6. 保持 `Reject Flipped Probe` 开启。
7. 执行 UV 优化。
8. 需要复核时点击 `Export Candidate Report`。

### 12.2 参数

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| Use Direction Probe | 关闭 | 是否使用已导入探针证据 |
| Probe Merge Bonus | 0.75 | 探针支持合并时的偏好强度 |
| Probe Confidence | 0.5 | 低于该置信度的证据被忽略 |
| Reject Flipped Probe | 开启 | 镜像或明确不可连接时保留接缝 |

### 12.3 JSON 示例

```json
{
  "schema": "vuv-probe-evidence-v1",
  "object": "UV_Test_Model",
  "source": "openSHIBIE",
  "edges": [
    {
      "vertices": [12, 18],
      "merge_score": 0.92,
      "confidence": 0.95,
      "direction_delta_deg": 4.5,
      "scale_ratio": 1.02,
      "phase_error": 0.03,
      "flipped": false,
      "can_merge": true
    }
  ]
}
```

`vertices` 是排序后的两个网格顶点索引。导入后不要改变拓扑，否则顶点索引
可能无法继续匹配。

探针不会绕过拓扑、拉伸、翻转和重叠检查；它只改变候选优先级或拒绝已知方向
错误的连接。

## 13. Interactive Mapping

`Export Viewer` 会把当前选中网格和 UV 导出为一个独立 HTML 文件。

用途：

- 在 UV 图和 3D 模型之间定位对应面。
- 检查 UV 岛包含哪些模型表面。
- 检查重叠 UV。
- 把映射结果发给不打开 Blender 的人员查看。

操作：

1. 选择一个或多个有 UV 的网格。
2. 点击 `Interactive Mapping > Export Viewer`。
3. 选择 `.html` 保存位置。
4. `Open After Export` 开启时会自动使用默认浏览器打开。

UV 视图：

- 左键：选择面。
- 在同一点重复左键：循环选择重叠面。
- 中键或右键拖动：平移。
- 滚轮：缩放。

3D 视图：

- 左键拖动：旋转。
- 中键或右键拖动：平移。
- 滚轮：缩放。

导出的 HTML 不依赖外部服务，可以单文件传递。

## 14. 结果验收

### 14.1 红面

- 红面所有 UV 坐标均为 `(0, 0)`。
- UV Editor 左下角只显示一个重叠点。
- 红面不应占用其他图集区域。

### 14.2 绿面和黄面

- 保持有限且可用的 UV 面积。
- 没有非预期镜像。
- 没有明显穿插或重叠。
- 重要区域纹理密度合理。
- 黄面可以比绿面密度低，但不应全部消失。

### 14.3 接缝和拉伸

- 可见光滑区域没有过度碎裂。
- 硬边、凹边和材质边界处接缝符合预期。
- 圆柱、圆锥和倒角带尽量连续。
- 复杂区域无严重拉伸。

## 15. 常见问题

### 15.1 找不到插件面板

检查：

1. 插件是否已勾选启用。
2. 当前区域是否为 3D Viewport。
3. 是否按 `N` 打开侧栏。
4. 是否打开 `UV Optimizer` 标签页。
5. Blender 是否仍缓存旧模块；重启后再看。

### 15.2 分析结果全部是红色

可能原因：

- 相机没有覆盖模型。
- 使用 `Selected` 但没有选中相机。
- 法线方向错误且开启了 `Ignore Backfaces`。
- 采样过低，没有覆盖小面。
- 模型与采样器位置不匹配。

建议：

1. 改用 `Auto 6` 并点击 `Create / Update`。
2. 使用 `Sphere / 256 / 3 x 3` 重新分析。
3. 检查法线。
4. 对必须保留的面使用 `Important`。

### 15.3 红面没有到 `(0, 0)`

检查：

1. 当前启用版本是否为 `0.5.2`。
2. 是否对正确的活动对象执行优化。
3. 面是否真的为红色或手动 `Hidden`。
4. 优化后是否又执行了其他 UV 操作。
5. 面板是否仍存在两个旧 Hidden 参数；如果存在就是旧版缓存。

### 15.4 出现共享 Mesh Data 错误

多个对象使用同一 Mesh Data 时，分析结果无法安全分别写入。

执行：

`Object > Relations > Make Single User > Object & Data`

然后重新分析。

### 15.5 优化时间过长

- 降低 `Merge Tests`。
- Sphere 使用 `64` 或 `128`。
- Coverage Grid 改为 `1 x 1`。
- Exterior Resolution 改为 `48` 或 `64`。
- 先在简化模型上测试参数。

### 15.6 UV 仍然较碎

- 提高 `Merge Tests`。
- 适度提高 `Merge Search Angle`。
- 确认 `Developable Bands` 开启。
- 检查是否有大量原始 Seam、材质边界或 Sharp 边阻止合并。
- 使用方向探针提供可连接边界证据。

### 15.7 优化后不满意

立即使用 Blender Undo，或恢复优化前备份。

建议每次只调整一个参数并重新运行，记录变化。

## 16. 插件写入的数据

插件会在网格面上使用以下属性：

| 属性 | 作用 |
| --- | --- |
| `vuv_visibility` | 当前生效的可见性 |
| `vuv_visibility_auto` | 自动分析原始结果 |
| `vuv_override` | Important、Auto、Hidden 人工覆盖 |
| `vuv_hit_count` | 分析射线命中次数 |
| `vuv_detail_score` | 局部细节密度评分 |
| `vuv_exterior_ratio` | Exterior Flood 外部接触比例 |
| `vuv_exterior_depth` | Exterior Flood 外部连通深度 |

## 17. 当前限制

- UV 优化一次只处理活动网格对象。
- 可见性分析可联合计算多个选中网格，但共享 Mesh Data 需要先单用户化。
- 插件本身不生成贴图方向证据；方向探针 JSON 需要外部流程提供。
- 方向探针依赖顶点索引，拓扑变化后需要重新生成。
- 特别复杂的凹多边形建议先三角化或整理拓扑。
- 自动结果仍需美术检查，不建议未经检查直接覆盖正式资产。

## 18. 卸载

1. 打开 `Edit > Preferences > Add-ons`。
2. 搜索 `Visibility Aware UV Optimizer`。
3. 取消勾选。
4. 点击 `Remove`。
5. 重启 Blender。

卸载插件不会自动删除网格上已写入的 UV、Seam 和自定义面属性。
