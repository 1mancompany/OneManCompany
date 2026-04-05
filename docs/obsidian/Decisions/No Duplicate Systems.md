---
tags: [decision, iron-law, critical]
source: memory/feedback_no_duplicate_systems.md
---

# Iron Law: No Duplicate Systems

**零容忍重复系统。发现即 Critical 缺陷，立即修复。**

## Rules

- 同一份数据不能有两条路径获取 (e.g. WS + REST polling same endpoint)
- 同一个逻辑不能在两个地方实现 (e.g. `_write_yaml` and inline `path.write_text`)
- 同一个常量/映射不能在两个文件定义

## When Found

1. Mark as **Critical defect** — same priority as P0 data integrity
2. Fix in current workflow, cannot defer
3. Choose one path, delete the other
4. Extract helper/utility if logic is repeated

## Prevention

- Search for similar functionality before writing new code
- Code review: actively scan for duplicate paths
- Prefer `store.save_*()` / `store.load_*()` over direct file I/O

## Why

CEO explicit requirement: "不要一样的事情做两遍". Duplicates cause: change one place forget another, behavior inconsistency, wasted performance.

## Related
- [[Design Principles]] — Principle #7
- [[Coding Standards]] — Code smell detection
