# 端到端集成模拟测试设施

本文件用于多模块联动验收：按项目约定的启动方式拉起 【示例-backend模块】、【示例-service模块】、【示例-frontend模块】 等依赖服务，观察主链路输出和日志，再关干净进程。

> 模板说明：接入真实项目后，把本节命令、场景名、端口和 API 路径替换为本仓库的实际启动脚本与验收口径。

## 启动前

1. 确认当前不在 `main` 上直接做代码改动。
2. 如依赖缺失，先在根目录和各子模块执行项目约定的依赖安装命令（例如 `uv sync`、`npm install`）。
3. 先清理旧进程：

```powershell
# 【示例】按项目实际启动器替换
python -m tools.dev_cli stop all
python -m tools.dev_cli status
```

4. 如需要只看本次启动日志，可先删除或备份 `runtime/logs/*.log`。

## 启动标准场景

```powershell
# 【示例】按项目实际场景配置替换
python -m tools.dev_cli start all --profile default
python -m tools.dev_cli status
```

通过标准：

- 本次改动涉及的核心服务至少应 running。
- 端口状态和 pid 文件不显示明显旧进程污染。

## 主链路输出验证

优先用 HTTP / CLI / 日志验证，图形化点击只在需要确认 UI 展示时补充。

```powershell
# 【示例】按项目实际健康检查接口替换
python -m tools.dev_cli query health
Invoke-RestMethod 'http://127.0.0.1:8080/api/health'
```

通过标准：

- 主链路请求可返回成功响应或明确的 idle/空态。
- 有 trace 或任务日志时，状态应为完成态或能解释为什么本轮未触发。
- trace 应能看到关键处理节点，或能解释为什么本轮未触发。
- 主功能验收要覆盖本次改动的用户可见路径，不能只看服务启动成功。

## 功能触发

- 改了某个 API、配置、UI 或业务规则，就要触发对应功能。
- 对异步任务或工作流相关改动，至少观察一次任务状态、trace 或业务日志。
- 对资源访问相关改动，关注权限拒绝、参数非法等是否符合任务预期：业务拒绝可以出现，但要能解释；框架错误不能进入下一阶段。

## 停止和清理

每次完整模拟结束必须关干净：

```powershell
python -m tools.dev_cli stop all
python -m tools.dev_cli status
```

通过标准：

- 已启动服务都停止。
- 后续 `status` 不显示本次遗留 pid。
- 端口占用异常要在 `process.md` 里记录并处理。
