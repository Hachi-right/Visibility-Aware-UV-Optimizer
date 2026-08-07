---
name: project-inbox-rules
description: 项目大脑收件规则。写收件、读收件格式、启动扫描当天收件、已处理精简文件头、或向 team-lead 发拍板类收件时使用。
---

# project-inbox-rules

本 skill 保存项目大脑收件机制的操作细节。冷启动行为约束见 `主流程.md` §2 R9；拍板发件门禁见 `design-advisor-lens`。

## 使用场景

- 向其他成员 `收件/` 写入同步信息、临时替班交接或审核汇报。
- 启动时扫描当天收件（只读文件头至 `---`）。
- 用户说「展开收件」「读收件 {文件名}」后读正文。
- 收件沉淀完成后压缩文件头为「已处理」。
- 向 `team-lead/收件/` 发拍板类收件（须先跑 `design-advisor-lens`）。

## 必读参考

| 文件 | 内容 |
| --- | --- |
| `references/header-format.md` | 文件头字段、已处理精简、正文分离 |
| `references/startup-scan.md` | 启动扫描与口述规则 |

## 读取方式

1. 写收件前读 `header-format.md`。
2. 启动扫描读 `startup-scan.md`。
3. 拍板类 → team-lead 前必须先执行 `design-advisor-lens`。

## 硬边界（不在此展开）

- 收件 ≠ task；不替代 `tasks/` 留痕。
- 发件人只写收件人 `收件/`；不得借收件改收件人其他私人文件。
- 启动只读当天文件头；正文须用户确认后才读。
