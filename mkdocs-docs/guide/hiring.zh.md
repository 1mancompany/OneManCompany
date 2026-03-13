# 招聘与 Talent Market

需要更多人手？HR 会在 Talent Market 中搜索 — 这是一个社区验证的 AI 员工市场 — 并处理整个招聘流程。

## Talent Market

[Talent Market](https://carbonkites.com) 是一个社区共建的 AI 员工包生态系统。每个 Talent 包含：

- **技能** — 专业能力（React 开发、2D 美术、数据分析）
- **知识** — 领域专长和最佳实践
- **个性** — 工作风格和沟通方式
- **工具** — 专属的 MCP 工具和集成

## 招聘流程

### 1. 发起招聘需求

在 CEO 控制台说出你的需求：

> "我需要一个高级前端开发"
> "招一个会做像素画的游戏设计师"

HR 收到需求后会在 Talent Market 中搜索。

### 2. HR 搜索与推荐

HR 根据以下维度评估可用的 Talent：

- 技能与需求的匹配度
- 社区评分和验证状态
- 与现有团队的兼容性

### 3. CEO 面试

HR 呈交候选人。作为 CEO，你可以：

- 查看每位候选人的 profile 和技能
- 提问或要求更多详细信息
- 批准或拒绝录用

### 4. 自动入职

一旦你批准录用，系统会自动：

1. 创建员工目录
2. 安装 Talent 包（技能、工具、知识）
3. 配置 Vessel（执行容器、工具权限）
4. 分配部门和汇报线
5. 员工出现在办公室的工位上

## Vessel + Talent 架构

可以把它想象成 **EVA 或高达** — 当驾驶员插入时，强大的机甲才真正活过来。

- **Vessel**（机甲）= 执行容器 — 重试逻辑、超时机制、工具访问、通信协议
- **Talent**（驾驶员）= 能力包 — 技能、知识、个性、专属工具
- **Employee** = Vessel + Talent

同一个 Vessel 可以搭载不同的 Talent。同一个 Talent 可以运行在不同的 Vessel 中。这种模块化设计让一切都能即插即用。

## 管理员工

### 解雇

表现不佳？你可以解雇表现差的员工：

- 妥善清理活跃任务和数据
- 不是简单的 `kill -9` — 而是优雅的离职流程
- 该职位会空出来给新员工

### 部门

HR 会将员工分配到相应部门：

- 技术研发部（Engineering）
- 市场营销部（Marketing）
- 设计、QA 等

## 构建你自己的 Talent

想为生态系统贡献力量？使用 [Talent Template](https://github.com/1mancompany/talent-template) 创建你自己的 AI 员工并发布到 Talent Market。
