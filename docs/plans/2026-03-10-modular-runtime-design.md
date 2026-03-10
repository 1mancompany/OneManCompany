# CompanyRuntime 模块化重构设计

> **状态**: 计划中，等后期大版本升级时实施

## 问题

当前所有核心对象（`company_state`、`tool_registry`、`event_bus`、`employee_configs`、`settings`）都是模块级单例，import 即初始化。导致：

- 无法独立启动单个 agent
- 无法在同一进程跑多个公司实例
- 测试必须 monkeypatch 全局变量
- 所有 agent 强依赖完整公司目录结构

## 目标架构

```
CompanyRuntime (harness)
├── config: CompanyConfig        # 替代全局 settings / employee_configs
├── state: CompanyState          # 替代全局 company_state 单例
├── tools: ToolRegistry          # 替代全局 tool_registry
├── events: EventBus             # 替代全局 event_bus
└── employees: EmployeeManager   # 管理所有 agent 实例
```

### 启动模式

**完整模式** — 替代现有 `main.py`：
```python
runtime = CompanyRuntime.from_dir("./company")
# 加载全部员工、工具、事件总线、API 服务
await runtime.start()
```

**最小模式** — 单 agent 独立运行：
```python
runtime = CompanyRuntime.minimal(
    employee_id="00004",
    employee_dir="./company/human_resource/employees/00004",
    api_key="sk-or-v1-...",
)
ea = runtime.create_agent("00004")
result = await ea.run("分析当前团队结构")
```

最小模式下：
- config: 只加载目标员工的 profile.yaml
- state: 空壳，只注册这一个员工
- tools: 只注册 base tools（不加载其他角色工具）
- events: 空 EventBus（无人监听不报错）

### 跨员工工具处理

跨员工工具（`dispatch_child`、`pull_meeting`、`report_to_ceo`）在最小模式下的两种策略：

**A. 本地降级**：目标员工不在本 runtime → 返回 error dict。适合纯独立任务。

**B. 远程代理**：通过 HTTP 转发到主公司服务器。适合分布式部署。
```
standalone agent ──HTTP──▶ 主公司 CompanyRuntime (full)
  dispatch_child()          ──▶ 转发到目标员工
  pull_meeting()            ──▶ 在主服务器上开会
  report_to_ceo()           ──▶ 推到主服务器 CEO 界面
```

两种策略都应支持，通过配置切换。

## 改造范围

### 核心变更

1. **依赖注入替代全局单例**
   - 所有 `from xxx import company_state` → `self.runtime.state`
   - 工具函数通过闭包或 contextvars 获取 runtime 引用
   - `make_llm()` 接收 config 参数而非读全局 settings

2. **ToolRegistry 实例化**
   - 每个 runtime 有独立的 ToolRegistry 实例
   - 工具注册在 runtime 初始化时按需执行，而非 import 时全量注册

3. **Agent 构造**
   - `BaseAgentRunner.__init__(runtime, employee_id)` 接收 runtime
   - `_build_prompt()` 从 runtime.config 读取，不读全局变量

4. **向后兼容层**（过渡期）
   - 保留全局 `company_state` 等作为 thin proxy 指向 default runtime
   - 逐步迁移后删除

### 影响文件（预估）

| 目录 | 文件数 | 改动类型 |
|------|--------|---------|
| `core/` | ~10 | 单例 → 实例方法 |
| `agents/` | ~8 | 构造函数注入 runtime |
| `api/` | ~3 | 从 runtime 取依赖 |
| `tools/` | ~2 | registry 实例化 |
| `tests/` | ~30+ | 去掉 monkeypatch，直接构造 runtime |

## 风险

- 改动面极大，几乎 touch 所有文件
- 工具闭包中的 runtime 引用需要仔细设计（避免循环引用）
- 过渡期全局 proxy 可能引入微妙 bug
- 建议在独立分支上做，充分测试后合入

## 里程碑

1. **M1**: `CompanyRuntime` 类 + `from_dir()` 工厂方法，内部仍用全局单例（兼容层）
2. **M2**: `ToolRegistry` 实例化，工具注册从 import-time 改为显式调用
3. **M3**: `CompanyState` 实例化，去掉全局 `company_state`
4. **M4**: Agent 依赖注入，去掉全局 `settings` / `employee_configs`
5. **M5**: `minimal()` 模式 + 远程代理策略
6. **M6**: 删除兼容层，清理全局单例
