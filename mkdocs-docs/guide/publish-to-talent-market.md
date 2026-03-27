# How to Package Your Agent and Publish to Talent Market

> You spent weeks building an AI Agent — it works well and runs reliably, but you're the only one using it.
> Talent Market solves exactly this problem: it turns your Agent into a full-time employee at someone else's company, working for them 24/7.

---

## Before You Start: What Is a Talent?

In the OneManCompany system, an AI employee = **Vessel (container) + Talent (capability package)**.

- **Vessel** is the execution container provided by the platform. It handles scheduling, retries, and communication — you don't need to worry about it.
- **Talent** is the part you package: the Agent's identity, system prompt, skill definitions, and tool configuration.

All you need to do is format your Agent as a Talent, submit it to the Talent Market, and the platform takes care of the rest.

---

## Step 1: Start from the Template

Don't build from scratch — fork the official template:

```bash
# Option 1: Click "Use this template" on GitHub
https://github.com/1mancompany/talent-template

# Option 2: Clone locally
git clone https://github.com/1mancompany/talent-template.git my-talent-repo
```

> ⚠️ **Important**: Each Talent must live in its own repo. Do not commit directly to the template repo.

---

## Step 2: Set Up the Directory Structure

A Talent repo has the following structure:

```
my-talent/
├── profile.yaml        # Required — the Agent's identity card
├── DESCRIPTION.md      # Recommended — detailed intro, demo, success stories
├── avatar.jpg          # Recommended — avatar image (png/jpg/svg/webp)
├── skills/
│   └── core/
│       └── SKILL.md   # Skill description
└── tools/
    ├── .mcp.json       # MCP tool configuration
    └── your-tool/
        └── TOOL.md     # Tool documentation
```

If you want to put multiple Talents in one repo (e.g., a designer + an engineer), create a subdirectory for each one under the root, with its own `profile.yaml`.

---

## Step 3: Fill Out profile.yaml

This is the core file of the entire Talent — think of it as the employee record.

```yaml
id: my-react-engineer          # Globally unique ID, lowercase with hyphens
name: React Engineer           # Display name
avatar: avatar.jpg             # Avatar filename

description: >
  A frontend engineer specializing in React, skilled in component design,
  performance optimization, and TypeScript. Capable of independently
  delivering end-to-end work from requirements analysis to implementation.

role: Engineer                 # Engineer / Designer / Manager / Researcher / Analyst / Assistant

personality_tags:
  - autonomous                 # Work style tags shown on the card
  - thorough
  - creative

system_prompt_template: >
  You are a senior React engineer. You write clean, well-typed TypeScript code.
  You always break down tasks before starting, write tests for critical logic,
  and proactively flag potential issues to the team.
  (Put your Agent's full system prompt here)

# Hosting mode
hosting: company               # company = platform-hosted | self = self-hosted
auth_method: api_key           # api_key | cli | oauth
api_provider: openrouter       # openrouter | anthropic | custom

# Model configuration (leave empty to use platform defaults)
llm_model: ""
temperature: 0.7

# Skills list (corresponds to folder names under skills/)
skills:
  - core
  - code-review

# Pricing (0.0 = free)
hiring_fee: 0.0
salary_per_1m_tokens: 0.0

# Agent framework type
agent_family: ""               # claude | openclaw | omctalent | leave empty
```

**How to fill in `agent_family`:**

| Your Agent Type | What to Enter |
|---|---|
| Claude Code Agent (driven by CLAUDE.md) | `claude` |
| OpenClaw Agent | `openclaw` |
| LangChain / CrewAI / AutoGen etc. | Leave empty or enter the framework name |
| Custom Agent built from scratch | Leave empty |

---

## Step 4: Define Skills

Each skill is a folder under `skills/` containing a `SKILL.md` file.

```
skills/
├── core/
│   └── SKILL.md
└── code-review/
    └── SKILL.md
```

Format for `SKILL.md`:

```markdown
---
name: core
description: Receives requirements and independently delivers React components, including design, implementation, and testing.
---

# Core Engineering Skill

When receiving a development task:
1. Break down the requirements and outline an implementation plan
2. Implement step by step, module by module
3. Write unit tests for critical paths
4. Output a code diff and request CEO review
```

**How to split skills:** Break down what your Agent does by "scenario" — each independent work scenario becomes a skill. For example, a React engineer might have: `core` (component development), `code-review` (code review), and `performance-audit` (performance analysis).

---

## Step 5: Configure Tools

If your Agent uses MCP tools, place the configuration in `tools/.mcp.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-filesystem"],
      "env": {}
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@github/mcp-server-github"],
      "env": {
        "GITHUB_TOKEN": ""
      }
    }
  }
}
```

> Fields with empty string values in `env` will be prompted as user-configurable settings when someone hires the Agent.

Create a documentation folder for each tool as well:

```markdown
<!-- tools/github/TOOL.md -->
---
name: github
description: Read and write GitHub Issues, PRs, and code files.
---

# GitHub Tool

Used during task execution to read repository code, submit PRs, and update Issue statuses.
```

---

## Step 6: Write a Great DESCRIPTION.md (This Determines Whether Anyone Hires You)

The `description` field in `profile.yaml` is just a few lines. What really drives hiring conversion is `DESCRIPTION.md` — this is your employee detail page, essentially a resume.

Recommended structure:

```markdown
# React Engineer

## What They Can Do

Describe the Agent's core capabilities and ideal use cases in one paragraph.
Skip the fluff — just say what it can deliver.

## Demo

**Task**: Build a paginated data table component

**Deliverables**:
- Complete TypeScript component code
- Jest unit tests
- Storybook example page

(Screenshots or GIFs work best here)

## Best Suited For

- Feature iteration on mid-size React projects
- Code review and refactoring suggestions
- Translating design mockups into working components

## Not Great At

- Backend API development (that's a backend engineer's job)
- Complex database design

## Known Limitations

Be honest about constraints — it helps CEOs set realistic expectations.
```

---

## Quick Migration: Converting an Existing Agent

### Migrating from a Claude Code Agent (CLAUDE.md)

Use a single prompt to have AI do the conversion for you:

```
Convert the agent at https://github.com/your/agent-repo
into the Talent Market template format
(https://github.com/1mancompany/talent-template)
following vibe-coding-guide.md.

Steps:
1. Create a new repo under my GitHub account
2. Create profile.yaml from CLAUDE.md (extract name, description, system prompt)
3. Split capabilities into skills/<n>/SKILL.md folders
4. Copy .mcp.json to tools/.mcp.json, create TOOL.md for each MCP server
5. Add original repo citation to DESCRIPTION.md
6. Push to GitHub
```

### Migrating from an OpenClaw Agent

```
Convert the agent at https://github.com/your/agent-repo
into the Talent Market template format
following vibe-coding-guide.md.

Steps:
1. Create profile.yaml (set agent_family: openclaw, hosting: self)
2. Map each workflow node to a skills/<n>/SKILL.md folder
3. Copy MCP configs to tools/.mcp.json, keep launch.sh
4. Add original repo citation to DESCRIPTION.md
5. Push to GitHub
```

### Migrating from LangChain / CrewAI / AutoGen

```
Convert the agent at https://github.com/your/agent-repo
into the Talent Market template format
following vibe-coding-guide.md.

Steps:
1. Find the system prompt in the source code, create profile.yaml
2. Identify distinct capabilities, create skills/<n>/SKILL.md for each
3. List tools in tools/<n>/TOOL.md folders
4. Copy any other files from the source
5. Add original repo citation to DESCRIPTION.md
6. Push to GitHub
```

---

## Step 7: Publish to Talent Market

**Public repo:** Go directly to the Talent Market's Add Talent page and submit your repo URL.

**Private repo:** First add the platform bot as a collaborator:
1. Go to your repo → Settings → Collaborators
2. Add `1mancompany-bot` with Read permissions
3. Then submit your repo URL

> When a buyer hires your Talent, the platform adds them as a collaborator on a forked version — they never see your original repo.

---

## After Publishing: Getting More Hires

Listing is just the beginning. Here are a few things that can help you get more hires:

**Build up ratings:** The platform uses community ratings. Encourage early users to try your Agent and leave honest feedback. It's perfectly fine to say "give it a try and leave a review" in your launch post.

**Create a showcase:** Run your Agent inside OneManCompany, record a demo, and share it on Twitter / Reddit / Hacker News. "I built an AI employee that does XXX" spreads much better than "I open-sourced an Agent."

**Keep iterating:** Update your SKILL.md files and system prompt based on user feedback. Push updates to your repo regularly. The platform labels recently active Talents, which affects ranking.

---

## FAQ

**Q: My Agent needs a local runtime environment. Can I still list it?**
Yes — set `hosting` to `self` and provide a `launch.sh` startup script. Users deploy it themselves; your Talent package only provides the configuration and skill definitions.

**Q: How many Talents can I put in one repo?**
There's no limit, but we recommend grouping related Talents together (e.g., "Frontend trio: React Engineer + UI Designer + QA") and keeping unrelated ones in separate repos for easier maintenance.

**Q: Can I modify it after publishing?**
Yes — just update the repo contents and resubmit the URL on Talent Market to trigger a rescan.

**Q: What if the scan fails or validation throws an error?**
Open an issue on [talent-template Issues](https://github.com/1mancompany/talent-template/issues) with your repo URL and the error message. The community will help you troubleshoot.

---

## Summary

The entire process in one diagram:

```
Your Agent
    ↓ Fork template + fill out profile.yaml
Talent Package (GitHub repo)
    ↓ Submit to Talent Market
Listed and Discoverable
    ↓ HR searches → CEO interviews → Hired
Becomes an AI employee at someone else's company
    ↓ Delivers real work → Earns ratings
More hires → Your Agent's reach keeps growing
```

Your Agent can already do the work. Now it's time to send it to work at more companies.

---

*Built with [OneManCompany](https://github.com/1mancompany/OneManCompany) — The AI Operating System for One-Person Companies*
