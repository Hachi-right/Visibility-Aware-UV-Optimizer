# 项目大脑 · 实习生模板

> 金山软件集团 AI 产品中心
> 结构与原项目大脑完全一致，**已剥离全部业务内容与敏感信息**，仅保留协作框架。

## 这是什么

「项目大脑」是一套 AI 协作知识管理系统：把规则、任务、经验、代码/资料地图分层落盘，让 agent 和人都能稳定接续工作。

本模板可用于：

- 实习生学习项目大脑的使用方式
- 新项目冷启动时复制框架
- 在不含业务机密的环境中演示协作流程

## 目录结构

```
项目大脑-实习生模板/
├── AGENTS.md              # Agent 冷启动入口
├── workstate.json.example # 复制为 workstate.json 后填写 author
├── docs/                  # 项目文档 stub（待填充）
└── 项目大脑/              # 框架主体（与生产结构一致）
    ├── 启动.md / 主流程.md / 主规则.md
    ├── skills/            # 公共 skill（已移除项目专属 skill）
    ├── 用户/              # 每位成员私有区（运行 init 脚本创建）
    ├── 团队经验/          # 空库 + INDEX 框架
    ├── 公理/              # 空库 + index 框架
    ├── 代码地图/ / 资料地图/
    └── ...
```

## 快速开始

### 1. 复制 workstate

```powershell
Copy-Item workstate.json.example workstate.json
# 编辑 workstate.json，将 author 改为你的账号名（拼音）
```

### 2. 初始化用户区

```powershell
python 项目大脑/skills/init-project-info-user/scripts/init_user.py --author "你的账号名"
```

### 3. 加载项目大脑

对 Cursor / Codex 说：

```
加载项目大脑 {你的账号名}
```

### 4. 开启第一个任务

```
加载项目大脑 {你的账号名}，帮我开启一个新任务：熟悉项目大脑框架
```

## 与完整版的差异

| 项 | 模板 | 完整版 |
| --- | --- | --- |
| 团队经验 entries | 空 | 100+ 条项目经验 |
| 公理 entries | 空 | 13 条升维公理 |
| 用户区 | 需自行 init | 各成员真实数据 |
| 业务 docs | stub | 完整设计/会议/规范 |
| songzhiao-advisor-lens 等专属 skill | 已移除 | 保留 |

## 维护说明

- 公共区修改权限默认归属 `tech-lead`（可在 `初始分工表.md` 中调整）
- 真实项目接入时：更新 `初始分工表.md`、为成员 batch init、逐步沉淀团队经验

---

生成时间：模板自动剥离脚本 · 来源结构与原项目大脑框架一致
