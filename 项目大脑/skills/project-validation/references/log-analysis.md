# 日志分析方法

本文件用于判断端到端集成模拟是否真的干净。日志位置和扫描规则以本文件为准。

> 模板说明：接入真实项目后，把日志路径、命令和典型错误关键字替换为本仓库的实际约定。

## 日志入口

| 来源 | 路径 / 命令 | 说明 |
| --- | --- | --- |
| 启动器日志 | `runtime/logs/*.log` | 项目启动器写入 |
| 单服务日志 | `python -m tools.dev_cli logs 【示例-service模块】 --tail 200` | 查看最近输出 |
| 任务 / 工作流日志 | `services/example/logs/` 或项目约定的 API | 异步任务、节点状态和结果 |
| 运行时目录 | `runtime/` 下各服务目录 | 部分服务自己的运行时日志 |

## 阻塞错误

出现以下内容通常不能进入下一阶段：

- `Traceback`
- `ERROR` / `CRITICAL`
- `Unhandled`
- `TypeError` / `ReferenceError` / `AssertionError`
- `Address already in use` / `EADDRINUSE`
- 项目约定的 workflow / task 失败关键字
- 协议解析失败、响应格式非法、必填字段缺失等框架契约错误

## 需要解释的业务拒绝

以下不是天然框架崩溃，但必须结合任务判断：

- 权限不足 / 参数非法 / 资源不存在
- HTTP 404，例如浏览器请求 `/favicon.ico`
- 可选依赖不可用但已降级处理的 warning

规则：

- 如果本任务预期不应有业务拒绝，相关日志要当成失败处理。
- 如果是可选上下文失败，必须有 warning 或 trace，可降级但不能静默。
- 如果业务函数、脚本回调或外部输出形状异常，必须有可定位日志；框架契约坏形状应失败。

## 脚本用法

```powershell
python 项目大脑/skills/project-validation/scripts/analyze_launcher_logs.py
python 项目大脑/skills/project-validation/scripts/analyze_launcher_logs.py --log-dir runtime/logs --tail-lines 2000
python 项目大脑/skills/project-validation/scripts/analyze_launcher_logs.py --fail-on-action-failed
```

输出字段：

| 字段 | 含义 |
| --- | --- |
| `blocking_count` | 阻塞错误数量；大于 0 时退出码为 1 |
| `action_failed_count` | 业务动作拒绝数量；默认不失败，传 `--fail-on-action-failed` 后失败 |
| `known_warning_count` | 已知可解释 warning 数量 |
| `files_scanned` | 实际扫描的日志文件 |

## 记录格式

写回 `process.md`：

- 扫描范围：哪些日志、是否只看本次启动段。
- 阻塞错误：0 或列出文件和关键行。
- 业务拒绝：是否符合任务预期。
- 已知 warning：为什么不阻塞。
- 结论：通过 / 不通过 / 需要人工判断。
