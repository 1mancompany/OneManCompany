#!/usr/bin/env bash
# launch_template.sh — Template for company-hosted employee task execution.
#
# SubprocessExecutor 调用约定:
#   $1 = employee_dir (包含 profile.yaml, skills/, progress.log 等)
#
# 环境变量（由 SubprocessExecutor 自动注入）:
#   OMC_EMPLOYEE_ID     — 员工 ID (如 "00010")
#   OMC_TASK_ID         — 当前任务 ID
#   OMC_PROJECT_ID      — 项目 ID
#   OMC_PROJECT_DIR     — 项目工作目录
#   OMC_TASK_DESCRIPTION — 任务描述（完整 prompt）
#   OMC_SERVER_URL      — 公司后端 URL (如 "http://localhost:8000")
#   OMC_MAX_ITERATIONS  — 最大迭代次数（默认 20）
#
# 输出约定:
#   stdout → JSON: {"output":"...", "model":"...", "input_tokens":N, "output_tokens":N}
#   stderr → 日志（不影响结果解析）
#   exit 0 → 成功，非零 → 失败
#
# 超时与取消:
#   SubprocessExecutor 管理超时（默认 3600s，由 TaskNode.timeout_seconds 配置）。
#   超时或取消时，进程收到 SIGTERM → 30s 内未退出则 SIGKILL。
#   脚本应响应 SIGTERM 做清理工作（trap EXIT 即可）。

set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"

MAX_ITERATIONS="${OMC_MAX_ITERATIONS:-20}"

# ---------------------------------------------------------------------------
# 清理钩子：进程被 SIGTERM 时执行
# ---------------------------------------------------------------------------
cleanup() {
    # 如果启动了 MCP server 等子进程，在这里清理
    if [ -n "${MCP_PID:-}" ] && kill -0 "$MCP_PID" 2>/dev/null; then
        kill "$MCP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

>&2 echo "[launch.sh] Employee=${OMC_EMPLOYEE_ID} Task=${OMC_TASK_ID} PID=$$"
>&2 echo "[launch.sh] Project=${OMC_PROJECT_ID} Dir=${OMC_PROJECT_DIR}"
>&2 echo "[launch.sh] Description: ${OMC_TASK_DESCRIPTION:0:200}"

# ---------------------------------------------------------------------------
# 可选：启动 MCP server 以提供公司工具
# ---------------------------------------------------------------------------
# 如果你的 talent 需要通过 MCP 调用公司工具，取消下面的注释：
#
# PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
# PYTHON="${PROJECT_ROOT}/.venv/bin/python"
#
# coproc MCP_PROC {
#     exec "$PYTHON" -m onemancompany.tools.mcp.server 2>/dev/null
# }
# MCP_PID=$MCP_PROC_PID
# >&2 echo "[launch.sh] MCP server PID=${MCP_PID}"

# ---------------------------------------------------------------------------
# 主逻辑：调用 LLM 完成任务
# ---------------------------------------------------------------------------
# 在此实现你的 agent loop。下面是用 curl + OpenRouter 的示例骨架：
#
# RESULT=$(curl -s https://openrouter.ai/api/v1/chat/completions \
#     -H "Authorization: Bearer ${OPENROUTER_API_KEY}" \
#     -H "Content-Type: application/json" \
#     -d "{
#       \"model\": \"${LLM_MODEL:-google/gemini-3.1-pro-preview}\",
#       \"messages\": [{\"role\": \"user\", \"content\": \"${OMC_TASK_DESCRIPTION}\"}]
#     }")
#
# OUTPUT=$(echo "$RESULT" | jq -r '.choices[0].message.content // "No output"')
# MODEL=$(echo "$RESULT" | jq -r '.model // ""')
# IN_TOKENS=$(echo "$RESULT" | jq -r '.usage.prompt_tokens // 0')
# OUT_TOKENS=$(echo "$RESULT" | jq -r '.usage.completion_tokens // 0')

# ---------------------------------------------------------------------------
# 输出 JSON 到 stdout（SubprocessExecutor 解析此格式）
# ---------------------------------------------------------------------------
# 替换为你的实际 agent 输出：
OUTPUT="Task completed"
MODEL=""
IN_TOKENS=0
OUT_TOKENS=0

cat <<EOF
{"output": $(echo "$OUTPUT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'), "model": "$MODEL", "input_tokens": $IN_TOKENS, "output_tokens": $OUT_TOKENS}
EOF
