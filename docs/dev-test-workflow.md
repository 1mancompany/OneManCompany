# Development & Testing Workflow

OneManCompany 开发测试标准流程，所有开发者（含 AI 开发者）必须遵循。

## 1. Git 分支规范

```
main (protected) ← PR only, no direct push
  └── fix/xxx or feat/xxx (feature branches)
```

- **禁止直接 push 到 main**，所有修改必须通过 PR 合并
- 分支命名：`fix/short-description` 或 `feat/short-description`
- 每个 PR 聚焦一个主题（可包含多个相关 bug fix）

## 2. 开发流程

### 2.1 创建分支

```bash
git checkout main
git pull origin main
git checkout -b fix/your-feature-name
```

### 2.2 编码规范

- 编码前必读 `vibe-coding-guide.md`
- TDD：先写测试，后写实现
- loguru `logger.debug()` 用 `{}` 格式，不用 `%s`
- 禁止静默 except：`except Exception: pass` 绝对禁止
- Mock patch 在 importing module 层级
- 详见 `vibe-coding-guide.md` 和项目 MEMORY 中的设计原则

### 2.3 提交

```bash
git add <specific-files>   # 不要用 git add -A
git commit -m "fix: 简要描述

详细说明（可选）

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

- commit message 用英文，格式：`fix:` / `feat:` / `refactor:` / `docs:` / `test:`
- pre-commit hook 会自动运行全量单元测试，测试不过则提交失败

## 3. Code Review 流程

### 3.1 创建 PR

```bash
git push origin fix/your-feature-name
gh pr create --title "fix: short description" --body "..."
```

### 3.2 Review 循环

PR 必须经过 code review，直到 reviewer 给出 **"Ready to merge"**：

```
提交 PR → Review → 发现问题 → 修复 → 再次 Review → ... → Ready to merge
```

Review 关注点：
- **Critical**：逻辑错误、运行时异常（如变量未定义）、安全漏洞（XSS 等）
- **Important**：代码一致性、edge case、性能问题
- **Suggestion**：代码风格、未来改进建议

规则：
- Critical 和 Important 必须修复后才能合并
- Suggestion 可以选择性采纳
- 每轮 review 修复后重新提交，再次 review

### 3.3 合并

```bash
gh pr merge <PR_NUMBER> --squash --delete-branch
```

## 4. 本地部署测试

### 4.0 首次初始化

首次部署或重置环境时需要初始化。有两种方式：

**交互式（推荐首次使用）：**
```bash
onemancompany-init          # 或 npx @1mancompany/onemancompany init
```

**自动模式（CI / 快速重建）：**
```bash
onemancompany-init --auto   # 或 npx @1mancompany/onemancompany init --auto
```

自动模式从项目根目录 `.env` 读取配置，跳过交互。**`.env` 必须包含：**

| 字段 | 必须 | 说明 |
|------|------|------|
| `OPENROUTER_API_KEY` 或 `ANTHROPIC_API_KEY` | ✅ | LLM 提供商 API Key |
| `DEFAULT_LLM_MODEL` | ✅ | 默认模型，如 `anthropic/claude-sonnet-4` |
| `HOST` | | 服务地址，默认 `0.0.0.0` |
| `PORT` | | 端口，默认 `8000` |
| `ANTHROPIC_API_KEY` | | Self-hosted 员工需要 |
| `SKILLSMP_API_KEY` | | FastSkills MCP 集成 |
| `TALENT_MARKET_API_KEY` | | 人才市场招聘 |

自动模式会显示配置摘要并要求二次确认后才执行。

### 4.1 启动服务

```bash
cd /path/to/OneManCompany
.venv/bin/python -m onemancompany.main
```

或使用 npx：
```bash
npx @1mancompany/onemancompany              # 后台运行
npx @1mancompany/onemancompany --debug      # 前台运行，显示日志
```

服务启动后访问 `http://localhost:8000`。

### 4.2 测试检查清单

部署后手动验证以下功能：

#### 基础功能
- [ ] 首页加载正常，像素风办公室渲染
- [ ] 员工列表显示，头像正常
- [ ] WebSocket 连接正常（浏览器 console 无报错）

#### Bug Fix 验证（按 PR 内容选择）
- [ ] 新员工入职后，部门区域颜色自动刷新（无需 F5）
- [ ] 任务树节点显示员工头像
- [ ] 任务树描述文字自动换行，不溢出
- [ ] 候选人卡片点击显示详情面板，关闭正常
- [ ] 修改员工 model 设置后生效

#### 安全检查
- [ ] 候选人名称含特殊字符（`<script>`）时不触发 XSS
- [ ] 所有用户输入在 innerHTML 中经过 escape

### 4.3 Debug 模式

```bash
OMC_DEBUG=1 .venv/bin/python -m onemancompany.main
```

- 日志输出到 `.onemancompany/logs/omc_YYYY-MM-DD.log`
- 包含 `[TASK LIFECYCLE]`、`[TASK PROMPT]`、`[TASK RESPONSE]` 等标签
- 可用 `grep` 过滤：`grep '\[TASK LIFECYCLE\]' .onemancompany/logs/omc_*.log`

## 5. 单元测试

```bash
# 运行全量测试
.venv/bin/python -m pytest tests/unit/ -v

# 运行单个测试文件
.venv/bin/python -m pytest tests/unit/api/test_routes.py -v

# 运行单个测试
.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestClassName::test_method -v

# 验证 import 无报错
.venv/bin/python -c "from onemancompany.main import app; print('OK')"
```

## 6. 完整开发周期示例

```
1. git checkout -b fix/my-bug
2. 写测试 → 写实现 → 本地测试通过
3. git commit（pre-commit 自动跑测试）
4. git push → gh pr create
5. Code Review 循环（可能多轮）
6. 本地部署测试（手动验证）
7. Review 通过 → gh pr merge --squash
8. git checkout main && git pull
```
