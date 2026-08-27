# VUV 0.5.5 发布验收标准

## 必须通过

- 发布 ZIP 只有一个 `visibility_uv_optimizer/` 顶层，精确包含 15 个白名单文件，且逐文件与 `addons/` 哈希一致；不得包含 `__pycache__`。
- `bl_info.version=(0, 5, 5)`，最低 Blender 为 3.3。
- 13 个 Python 模块全部通过 `py_compile`。
- Blender 3.3.5 和 5.2.0 LTS 的分组布局回归均返回 `VUV_GROUP_LAYOUT_REGRESSION_OK`，Unique 局部修复均返回 `VUV_UNIQUE_LOCAL_REPAIR_OK`，strict smoke 均返回 `VUV_055_SMOKE_OK`。
- 拼接武器 v10 独立 postflight 顶层 `passed=true`、`failures=[]`，151 项检查通过；所有目标 Mesh 的 overlap、负 winding、源退化和 UV 退化均为 0。
- Body 与 Gun Head 的 `orientation_components_failed`、`repeat_groups_failed`、`repeat_owner_pairs_failed`、`small_anchor_pairs_failed` 均为 0。
- `release/SHA256SUMS.txt` 全部匹配，根 README 和文档链接存在。
- 待推范围不包含 `.blend`、`.blend1`、现场 manifest/SVG/audit、MCP/诊断脚本、缓存、临时测试文件或秘密；验证记录中用于定位用户现场证据的绝对路径除外。
- 推送前远端 `uv展开` 没有本地未包含的新提交。
- 推送后重新 fetch，远端 `refs/remotes/origin/uv展开` 与本地 HEAD 一致。

## 允许的已知限制

- 0.5.5 仍是受控试验版；拼接武器验证通过不代表所有硬表面资产都能自动修复，源几何退化仍会被严格拒绝并回滚。
- v10 场景构建与最终发布包使用相同 15 文件指纹 `c9ad32c4f3388ed3ad6c81d67cbcdb0960afd5f26188488803210cb61b259f53`；最终发布源码已重跑两版 Blender 三组回归。
- Git 仓库不包含体积较大的 Blender 验证场景，验证结论由 smoke JSON 和验证记录承载。
- `main` 不在本任务范围内，必须保持不变。
