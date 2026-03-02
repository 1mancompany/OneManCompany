# Tool Cleanup Request (keep only opensandbox)

## Current tools under `assets/tools/`
- opensandbox (keep)
- 协作工具 (remove)
- 协作工具_78ee9a50 (remove)
- 数据可视化工具 (remove)
- 机器学习工具包 (remove)
- 标准办公电脑 (remove)
- 运营数据可视化工具 (remove)
- 项目管理工具 (remove)
- 高性能计算服务器 (remove)

## Limitation
Current COO toolset supports listing/registering/granting/revoking, but **does not provide a delete/remove API** for assets/tools directories.

## Recommended action (manual by CEO / maintainer)
Delete the following directories:
- assets/tools/协作工具
- assets/tools/协作工具_78ee9a50
- assets/tools/数据可视化工具
- assets/tools/机器学习工具包
- assets/tools/标准办公电脑
- assets/tools/运营数据可视化工具
- assets/tools/项目管理工具
- assets/tools/高性能计算服务器

Example commands:
```bash
rm -rf "assets/tools/协作工具" \
       "assets/tools/协作工具_78ee9a50" \
       "assets/tools/数据可视化工具" \
       "assets/tools/机器学习工具包" \
       "assets/tools/标准办公电脑" \
       "assets/tools/运营数据可视化工具" \
       "assets/tools/项目管理工具" \
       "assets/tools/高性能计算服务器"
```

## Optional improvement
Add an official `remove_asset(tool_id)` function to enforce clean deprovisioning with audit logs.
