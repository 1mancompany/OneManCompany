#!/usr/bin/env python3
"""Standalone runner template for company-hosted employees.

Generated in each employee's directory during onboarding.
Runs a LangChain ReAct agent with skills loaded from the local skills/ folder —
no dependency on the OneManCompany backend.

Usage:
    python run.py "Analyze the architecture of this project"
    echo "Write a proposal" | python run.py
    python run.py  # interactive mode
"""

from pathlib import Path

from onemancompany.core.config import ENCODING_UTF8


TEMPLATE = r'''#!/usr/bin/env python3
"""Standalone agent runner for {employee_name} ({employee_id}).

Self-contained LangChain agent with skills from skills/ folder.
No dependency on OneManCompany backend.

Usage:
    python run.py "your task here"
    python run.py              # interactive mode (reads from stdin)
"""

from __future__ import annotations

import os
import sys
import json
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — loaded from profile.yaml in same directory
# ---------------------------------------------------------------------------

_DIR = Path(__file__).parent
_PROFILE = _DIR / "profile.yaml"


def _load_profile() -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML required: pip install pyyaml")
    if not _PROFILE.exists():
        sys.exit(f"Missing {{_PROFILE}}")
    return yaml.safe_load(_PROFILE.read_text(encoding="utf-8")) or {{}}


def _load_llm(profile: dict):
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        sys.exit("langchain-openai required: pip install langchain-openai")

    # Provider registry — OpenAI-compatible providers just need base_url + env key
    _PROVIDERS = {{
        "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai"),
        "openai":     ("https://api.openai.com/v1", "OPENAI_API_KEY", "openai"),
        "anthropic":  ("", "ANTHROPIC_API_KEY", "anthropic"),
        "kimi":       ("https://api.moonshot.cn/v1", "KIMI_API_KEY", "openai"),
        "deepseek":   ("https://api.deepseek.com", "DEEPSEEK_API_KEY", "openai"),
        "qwen":       ("https://dashscope.aliyuncs.com/compatible-mode/v1", "QWEN_API_KEY", "openai"),
        "zhipu":      ("https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY", "openai"),
        "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai"),
        "together":   ("https://api.together.xyz/v1", "TOGETHER_API_KEY", "openai"),
    }}

    provider_name = profile.get("api_provider", "openrouter")
    model = profile.get("llm_model", "")
    temperature = profile.get("temperature", 0.7)
    api_key = profile.get("api_key", "")

    prov = _PROVIDERS.get(provider_name)

    if prov and prov[2] == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            sys.exit("langchain-anthropic required: pip install langchain-anthropic")
        key = api_key or os.environ.get(prov[1], "")
        if not key:
            sys.exit("Set " + prov[1] + " or api_key in profile.yaml")
        return ChatAnthropic(model=model, api_key=key, temperature=temperature)

    if prov and prov[2] == "openai":
        key = api_key or os.environ.get(prov[1], "")
        if not key:
            sys.exit("Set " + prov[1] + " or api_key in profile.yaml")
        base_url = prov[0]
        if provider_name == "openrouter":
            base_url = os.environ.get("OPENROUTER_BASE_URL", base_url)
        return ChatOpenAI(model=model, api_key=key, base_url=base_url, temperature=temperature)

    # Unknown provider — fall back to OpenRouter
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        sys.exit("Set OPENROUTER_API_KEY or api_key in profile.yaml")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return ChatOpenAI(model=model, api_key=key, base_url=base_url, temperature=temperature)


# ---------------------------------------------------------------------------
# Skills — loaded from skills/<name>/SKILL.md
# ---------------------------------------------------------------------------

def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {{}}, raw
    end = raw.find("---", 3)
    if end == -1:
        return {{}}, raw
    import yaml
    try:
        meta = yaml.safe_load(raw[3:end]) or {{}}
    except Exception:
        meta = {{}}
    return meta, raw[end + 3:].lstrip("\\n")


def _load_skills() -> dict[str, tuple[dict, str]]:
    """Return {{name: (meta, body)}} for all skills."""
    skills_dir = _DIR / "skills"
    if not skills_dir.exists():
        return {{}}
    result = {{}}
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                raw = skill_md.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(raw)
                result[entry.name] = (meta, body)
    return result


def _build_skills_prompt(skills: dict) -> str:
    """Build prompt: autoload skills inline, others as catalog."""
    autoloaded = []
    catalog = []
    for name, (meta, body) in skills.items():
        display = meta.get("name", name)
        desc = meta.get("description", "")
        if meta.get("autoload"):
            autoloaded.append(f"### {{display}}\\n{{body}}")
        else:
            line = f"- **{{display}}**"
            if desc:
                line += f": {{desc}}"
            catalog.append(line)

    parts = []
    if autoloaded:
        parts.append("## Active Skills")
        parts.extend(autoloaded)
    if catalog:
        parts.append("\\n## Available Skills")
        parts.append("Use the `load_skill` tool to load full instructions before applying.\\n")
        parts.extend(catalog)
    return "\\n".join(parts)


# ---------------------------------------------------------------------------
# Tools — lightweight built-in tools
# ---------------------------------------------------------------------------

def _make_tools(skills: dict) -> list:
    from langchain_core.tools import tool

    @tool
    def read_file(file_path: str) -> str:
        """Read a file and return its contents."""
        p = Path(file_path).expanduser()
        if not p.exists():
            return f"File not found: {{file_path}}"
        return p.read_text(encoding="utf-8")[:50000]

    @tool
    def write_file(file_path: str, content: str) -> str:
        """Write content to a file."""
        p = Path(file_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {{len(content)}} chars to {{file_path}}"

    @tool
    def list_dir(path: str = ".") -> str:
        """List files in a directory."""
        p = Path(path).expanduser()
        if not p.exists():
            return f"Directory not found: {{path}}"
        entries = sorted(p.iterdir())
        return "\\n".join(
            f"{{('d' if e.is_dir() else 'f')}}  {{e.name}}" for e in entries[:200]
        )

    @tool
    def bash(command: str) -> str:
        """Run a shell command and return output."""
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=120,
            )
            out = result.stdout
            if result.returncode != 0:
                out += f"\\n[exit code {{result.returncode}}]\\n{{result.stderr}}"
            return out[:20000]
        except subprocess.TimeoutExpired:
            return "[command timed out after 120s]"

    @tool
    def load_skill(skill_name: str) -> str:
        """Load a skill's full instructions by name from the Available Skills list."""
        if skill_name not in skills:
            return f"Skill '{{skill_name}}' not found. Available: {{list(skills.keys())}}"
        meta, body = skills[skill_name]
        return body

    return [read_file, write_file, list_dir, bash, load_skill]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_system_prompt(profile: dict, skills_prompt: str) -> str:
    name = profile.get("name", "Agent")
    role = profile.get("role", "Employee")
    prompt_template = profile.get("system_prompt_template", "")
    parts = []
    if prompt_template:
        parts.append(prompt_template)
    else:
        parts.append(f"You are {{name}}, role: {{role}}.")
    if skills_prompt:
        parts.append(skills_prompt)
    return "\\n\\n".join(parts)


def main():
    import asyncio
    profile = _load_profile()
    llm = _load_llm(profile)
    skills = _load_skills()
    skills_prompt = _build_skills_prompt(skills)
    system_prompt = _build_system_prompt(profile, skills_prompt)

    tools = _make_tools(skills)

    try:
        from langgraph.prebuilt import create_react_agent
    except ImportError:
        sys.exit("langgraph required: pip install langgraph")
    from langchain_core.messages import HumanMessage, SystemMessage

    agent = create_react_agent(model=llm, tools=tools)

    # Get task from args or stdin
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    else:
        print(f"[{{profile.get('name', 'Agent')}}] Interactive mode. Type your task (Ctrl+D to send):")
        task = sys.stdin.read().strip()

    if not task:
        sys.exit("No task provided.")

    async def run():
        result = await agent.ainvoke({{
            "messages": [
                SystemMessage(content=system_prompt),
                HumanMessage(content=task),
            ]
        }})
        # Walk backwards to find last AIMessage with text content
        from langchain_core.messages import AIMessage
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage):
                c = msg.content
                if isinstance(c, str) and c.strip():
                    return c
                if isinstance(c, list):
                    texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                    joined = "\n".join(texts)
                    if joined.strip():
                        return joined
        return str(result["messages"][-1].content) if result.get("messages") else ""

    output = asyncio.run(run())
    print(output)


if __name__ == "__main__":
    main()
'''


def generate_run_py(employee_dir: str | Path, employee_name: str, employee_id: str) -> Path:
    """Generate a standalone run.py in the employee directory."""
    dest = Path(employee_dir) / "run.py"
    content = TEMPLATE.format(employee_name=employee_name, employee_id=employee_id)
    dest.write_text(content, encoding=ENCODING_UTF8)
    dest.chmod(0o755)
    return dest
