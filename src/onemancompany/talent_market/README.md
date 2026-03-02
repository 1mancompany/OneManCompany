# Talent Market

The talent market provides structured **talent packages** for hiring and a
**remote worker protocol** for running agents on external nodes.

## Talent Package Structure

Each talent lives in `talents/{talent_id}/`:

```
talents/
  coding/
    profile.yaml          # metadata + defaults (role, LLM, personality)
    skills/
      sandbox_coding.md   # skill description (copied to employee on hire)
      web_research.md
    tools/
      manifest.yaml       # declares builtin + custom tools
      custom_build.py     # example custom LangChain @tool
```

### `profile.yaml`

| Field | Description |
|---|---|
| `id` | Unique talent identifier |
| `name` | Human-readable name |
| `description` | What this talent does |
| `role` | Default role type (Engineer, Designer, …) |
| `remote` | `true` = remote worker, `false` = on-site employee |
| `llm_model` | Default LLM model for this talent |
| `temperature` | LLM temperature |
| `skills` | List of skill names (matching `.md` files in `skills/`) |
| `tools` | List of tool names used by this talent |
| `personality_tags` | Personality trait tags |
| `system_prompt_template` | Template for the agent system prompt |

### `tools/manifest.yaml`

Declares which tools the talent uses:

- **`builtin_tools`** — names of registered LangChain tools (e.g. sandbox tools)
- **`custom_tools`** — names of `.py` files in the same directory, each
  exporting a `@tool`-decorated function

### Creating a Custom Talent

1. Create a new directory under `talents/`, e.g. `talents/data_analyst/`.
2. Add a `profile.yaml` with the required fields.
3. Add skill descriptions as `.md` files in `skills/`.
4. Add a `tools/manifest.yaml` listing the tools.
5. Optionally add custom `.py` tool files in `tools/`.

---

## Hiring Modes

### On-Site (`remote: false`)

When a candidate sourced from a talent is hired on-site:

- An employee folder is created under `company/human_resource/employees/{id}/`
- The talent's `skills/` markdown files are **copied** into the employee's
  `skills/` directory
- The talent's `tools/` (manifest + custom `.py`) are **copied** into the
  employee's `tools/` directory
- The employee operates as a normal LangChain agent within the server

### Remote (`remote: true`)

When a candidate is hired as a remote worker:

- A minimal employee folder is created (`profile.yaml` with `remote: true`)
- Skills and tools are **not** copied locally — the remote worker provides its own
- The employee appears on the office map with a "remote" indicator
- Tasks are dispatched via the remote worker HTTP protocol

---

## Remote Worker Protocol

Remote workers communicate with the company server via four HTTP endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/remote/register` | POST | Worker registers itself |
| `/api/remote/tasks/{employee_id}` | GET | Poll for pending tasks |
| `/api/remote/results` | POST | Submit task results |
| `/api/remote/heartbeat` | POST | Keep-alive signal |

### Flow

1. **Register** — Worker starts up and POST to `/api/remote/register` with its
   `employee_id`, callback URL, and capabilities.
2. **Poll** — Worker periodically GETs `/api/remote/tasks/{employee_id}`. If a
   task is available, the server returns a `TaskAssignment` object.
3. **Execute** — Worker processes the task using its own tools and environment.
4. **Submit** — Worker POSTs a `TaskResult` to `/api/remote/results`.
5. **Heartbeat** — Worker periodically POSTs to `/api/remote/heartbeat` to
   signal liveness.

### Data Models

See `remote_protocol.py` for the Pydantic models:

- `RemoteWorkerRegistration`
- `TaskAssignment`
- `TaskResult`
- `HeartbeatPayload`

---

## Extending `RemoteWorkerBase`

The `remote_worker_base.py` module provides an abstract base class for building
remote workers.

```python
from onemancompany.talent_market.remote_worker_base import RemoteWorkerBase
from onemancompany.talent_market.remote_protocol import TaskAssignment, TaskResult


class MyCodingWorker(RemoteWorkerBase):
    def setup_tools(self) -> list:
        # Return LangChain tools this worker uses
        return [my_sandbox_tool, my_web_search_tool]

    async def process_task(self, task: TaskAssignment) -> TaskResult:
        # Process the task ...
        return TaskResult(
            task_id=task.task_id,
            employee_id=self.employee_id,
            status="completed",
            output="Task done!",
        )


# Run the worker
import asyncio

worker = MyCodingWorker(
    company_url="http://localhost:8000",
    employee_id="00010",
    capabilities=["coding", "web_research"],
)
asyncio.run(worker.start())
```

The base class handles registration, task polling, and heartbeat loops
automatically. You only need to implement `setup_tools()` and `process_task()`.
