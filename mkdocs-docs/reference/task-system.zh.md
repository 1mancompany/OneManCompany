# 任务状态系统

所有任务共享统一的 `TaskPhase` 状态机：

```
pending → processing ⇄ holding → completed → accepted → finished
              ↓                       ↓
           failed ──(retry)──→ processing

pending/holding → blocked（依赖失败）
any non-terminal → cancelled
```

| 状态 | 含义 |
|------|------|
| `pending` | 已创建，等待启动 |
| `processing` | Agent 正在执行 |
| `holding` | 等待子任务 / CEO 回复 |
| `completed` | 执行完毕，等待上级审核 |
| `accepted` | 上级通过（解除下游阻塞） |
| `finished` | 复盘后归档 |
| `failed` | 执行失败（可重试） |
| `blocked` | 依赖失败 |
| `cancelled` | 被 CEO 或上级取消 |

**Simple vs Project 任务**使用同一套状态机，区别在于自动跳过：
- **Simple**：`completed` → 自动 `accepted` → 自动 `finished`
- **Project**：`completed` → 手动审核 → EA 复盘 → `finished`

所有状态变更必须经过 `task_lifecycle.py` 的 `transition()` 方法。
