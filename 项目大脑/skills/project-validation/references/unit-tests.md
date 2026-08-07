# 单元测试方法和基础设施

本文件用于选择单元测试集合。原则是先覆盖被改代码，再按风险扩大范围。

> 模板说明：接入真实项目后，把下表中的模块路径和命令替换为本仓库的实际测试入口。

## 测试入口

| 改动范围 | 推荐命令 | 说明 |
| --- | --- | --- |
| 根工程 / 协议 / 启动器 / 数据层 | `python -m pytest tests/unit -q` | 根工程 pytest 配置在 `pyproject.toml` |
| 单个根工程测试 | `python -m pytest tests/unit/test_xxx.py -q` | 小改动先跑定向测试，再按风险补集合 |
| 【示例-service模块】 | `Push-Location services/example; uv run --with pytest python -m pytest tests -q; Pop-Location` | 独立子模块有单独依赖时使用 |
| 【示例-service模块】 定向 | `Push-Location services/example; uv run --with pytest python -m pytest tests/test_xxx.py -q; Pop-Location` | 用于局部业务逻辑、接口或规则改动 |
| 前端工程 | `Push-Location frontend; npm run build; Pop-Location` | 当前若无统一 JS 单测入口，build 是基础门槛 |

## 覆盖要求

- 单元测试必须触发改动到的功能入口；只跑不触达的测试不算充分验收。
- 修改核心业务链路时，至少覆盖相关模块的定向测试。
- 修改防御式检查或框架错误处理时，必须覆盖错误时输出、业务回调不崩、框架契约错误会失败且信息有用。
- 修改启动器或集成脚本时，必须跑对应单测；完整链路再看 `launcher-simulation.md`。
- 修改代码规范、项目大脑 skill 或脚本时，必须跑对应脚本的 `py_compile`、自测命令和 `git diff --check`。

## 记录格式

写回当前任务 `process.md` 时使用：

| 命令 | 结果 | 覆盖了什么 | 未覆盖风险 |
| --- | --- | --- | --- |
| `<command>` | 通过 / 失败 | 具体功能入口 | 没跑的原因或后续补测 |
