# Memento-OneManCompany

**The AI Operating System for One-Person Companies**

> Others use AI to write code. You use AI to run a company.
>
> If Linux is the OS for servers, Memento-OneManCompany is the OS for companies.

Memento-OneManCompany is an open-source OS that lets anyone build and run a complete AI-powered company from their browser. You are the CEO — the only human. Everyone else — HR, COO, engineers, designers — are AI employees that think, collaborate, and deliver real work autonomously.

Yes, your AI employees have performance reviews. Yes, they get nervous.

Tired of auto-generated AI agents that confidently produce nonsense? Memento-OneManCompany ships with a **Talent Market** — community-verified AI employees that actually deliver, not hallucination machines.

[中文文档](README_zh.md)

<p align="center">
  <a href="https://carbonkites.com">
    <img src="img/talent-market-icon.png" alt="Talent Market" height="28" style="vertical-align: middle;" />
    <b>Talent Market</b>
  </a>
</p>

---

## Why Memento-OneManCompany?

Today's AI tools help you do individual tasks — write an email, generate an image, fix a bug. Cute. Memento-OneManCompany gives you **an entire organization.**

- **Not a chatbot** — a company with org structure, hiring, task management, performance reviews, and knowledge management
- **Not a demo** — delivers production-grade output (games, comics, apps — not "here's a draft, good luck")
- **Not a framework** — a complete platform you can run from your browser, no code required

### What You Can Build


| AI Company           | What It Delivers                                                  |
| -------------------- | ----------------------------------------------------------------- |
| 🎮 AI Game Studio    | Production-grade games with full playtesting and iteration cycles |
| 📖 AI Manga Studio   | Serialized comic stories with consistent art and narrative        |
| 💻 AI Dev Agency     | Ship software products end-to-end                                 |
| 🎨 AI Content Studio | Marketing campaigns, branded content, and media production        |
| 🔬 AI Research Lab   | Literature review, data analysis, and report generation           |


These aren't toy demos — each AI company produces **product-level deliverables** through a full team of collaborating AI agents.

### How We're Different


|                                | Typical Agent Orchestrators          | OneManCompany                                                                                                                                     |
| ------------------------------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Agent architecture**         | Flat task runners, BYOA              | Vessel + Talent separation — deep modular architecture with 6 Harness protocols and 3-tier customization                                          |
| **Where do agents come from?** | You find and configure them yourself | **Founding C-suite built-in on Day 1**. Other employees hired by HR from a community-verified **Talent Market** — no more hunting for good agents |
| **Execution model**            | Heartbeat polling / loop             | Event-driven, zero-idle, on-demand dispatch                                                                                                       |
| **Organization**               | Simple task queues                   | Full Fortune 500-style company simulation (see below)                                                                                             |
| **Deliverables**               | Single-point task outputs            | Production-grade, multi-iteration project delivery with quality gates                                                                             |


### Built Like a Real Company

We didn't just borrow corporate vocabulary — we faithfully modeled how Fortune 500 companies actually operate:

- **Org chart & reporting lines** — hierarchical management, department-based structure
- **Hiring & onboarding** — HR searches Talent Market, CEO interviews, automated onboarding flow
- **Firing & offboarding** — yes, you can fire underperformers (with proper cleanup, not just `kill -9`)
- **Performance reviews** — quarterly scoring, probation, PIP, promotion tracks
- **Task delegation & approval chains** — CEO → executives → employees, with quality gates at every level
- **Meeting rooms** — multi-agent synchronous discussions with meeting reports
- **Knowledge base & SOPs** — company culture, direction docs, workflow definitions
- **File change approvals** — employees propose edits, CEO reviews diffs and approves in batch
- **Cost accounting** — per-project LLM token usage and USD cost tracking
- **1-on-1 coaching** — CEO guidance sessions that permanently shape employee behavior
- **Hot reload & graceful restart** — zero-downtime deployments for AI companies

Something missing? [Open an issue](https://github.com/CarbonKite/OneManCompany/issues) or build it yourself — that's the beauty of open source.

### Why It's an OS, Not Just a Company

Memento-OneManCompany doesn't build **a** company — it lets you build **any** company. Three features make this possible:

1. **Company Direction** — Your vision statement is injected into every employee's reasoning. Change the direction, the entire company pivots.
2. **Company Culture** — Behavioral principles that govern every employee. Same Talents, same Vessels, completely different company personality.
3. **Vessel + Talent** — Modular architecture that makes everything swappable. Same OS, same founding team, different Talents from the marketplace.

---

## Features




|                                                                      |                                                               |
| -------------------------------------------------------------------- | ------------------------------------------------------------- |
| **Task Tree** — Hierarchical task breakdown with dependency tracking | **Task Management** — CEO reviews and approves at every level |


---

## How It Works

You open a browser. You see a pixel-art office. Your AI employees are at their desks, pretending to look busy.

You type: *"Build a puzzle game for mobile"*

1. Your **EA** receives the task and routes it
2. Your **COO** breaks it down and dispatches subtasks
3. Engineers, designers, and QA **work autonomously**
4. They hold **meetings** to align when needed
5. Work goes through **review, iteration, and quality gates**
6. You get notified and approve the final result

**You manage. AI executes.**

```
CEO (You, the only human who gets coffee breaks)
  └── EA ── routes tasks, quality gate
        ├── HR ── hiring, performance reviews, promotions
        ├── COO ── operations, task dispatch, acceptance
        │    ├── Engineer (AI)  ← hired from Talent Market
        │    ├── Designer (AI)  ← hired from Talent Market
        │    └── QA (AI)        ← hired from Talent Market
        └── CSO ── sales, client relations
```

**Founding team (EA, HR, COO, CSO)** comes built-in. Need more people? HR searches the **Talent Market** — a community-verified marketplace of AI employees.

### The Vessel + Talent System

Think of it like **EVA or Gundam** — a powerful mech that comes alive when a pilot is plugged in.

- **Vessel** (the mech) = execution container. Defines how an employee runs: retry logic, timeouts, tool access, communication protocols.
- **Talent** (the pilot) = capability package. Brings skills, knowledge, personality, and specialized tools.
- **Employee** = Vessel + Talent. Hire from the Talent Market, and the system handles the rest.

> For a deep dive into the Vessel architecture, see [docs/vessel-system.md](docs/vessel-system.md).

---

## Quick Start

### Prerequisites

You only need **Node.js 16+** and **Git**. Everything else (UV, Python 3.12, dependencies) is installed automatically.

<details>
<summary><b>macOS</b></summary>

```bash
# Install Git (if not already installed)
xcode-select --install

# Launch (auto-installs UV + Python 3.12 + dependencies)
npx @carbonkite/onemancompany
```

</details>

<details>
<summary><b>Windows</b></summary>

```powershell
# Install Git: https://git-scm.com/download/win
# Install Node.js: https://nodejs.org/

# Launch (auto-installs UV + Python 3.12 + dependencies)
npx @carbonkite/onemancompany
```

</details>

<details>
<summary><b>Linux (Ubuntu/Debian)</b></summary>

```bash
# Install prerequisites
sudo apt update && sudo apt install -y git nodejs npm

# Launch (auto-installs UV + Python 3.12 + dependencies)
npx @carbonkite/onemancompany
```

</details>

First run automatically:
1. Installs **UV** (fast Python package manager)
2. Installs **Python 3.12** via UV (isolated, no system changes)
3. Clones the repository
4. Creates venv and installs dependencies
5. Launches the setup wizard (API keys, Talent Market config)

Then open `http://localhost:8000`. Congratulations, you're a CEO now.

### Start Again Later

```bash
# Option 1: npx again (auto-updates if there's a new version)
npx @carbonkite/onemancompany

# Option 2: run directly from the cloned directory
cd OneManCompany
bash start.sh
```

### Manual Install

```bash
# 1. Clone
git clone https://github.com/CarbonKite/OneManCompany.git
cd OneManCompany

# 2. Start (auto-installs UV + Python if needed, then runs setup wizard on first launch)
bash start.sh

# 3. Open browser
open http://localhost:8000    # macOS
# xdg-open http://localhost:8000  # Linux
# start http://localhost:8000     # Windows
```

### Restart / Reconfigure

```bash
# Restart server
bash start.sh

# Custom port
bash start.sh --port 8080

# Re-run setup wizard (change API keys, etc.)
bash start.sh init
```

### Configuration Files

| File                         | Purpose                                |
| ---------------------------- | -------------------------------------- |
| `.onemancompany/.env`        | API keys (OpenRouter, Anthropic, etc.) |
| `.onemancompany/config.yaml` | App config (Talent Market URL, etc.)   |
| Browser Settings panel       | Frontend preferences                   |


---

## Vision & Roadmap

**Near-term:** Enable 100 AI one-person companies within one year.

**Long-term:** Redefine the relationship between AI, humans, and organizations.


| Tier                        | Focus                                 | Examples                                                                 |
| --------------------------- | ------------------------------------- | ------------------------------------------------------------------------ |
| 🔧 **Stronger AI Agents**   | Make each employee more capable       | Enhanced sandbox, better tool usage, improved code execution             |
| 🏢 **Smarter Organization** | Make the company run more efficiently | CEO experience, advanced task scheduling, multi-agent collaboration      |
| 🌐 **AI-Native Ecosystem**  | Build a thriving open ecosystem       | Talent Market expansion, third-party tools/APIs, community contributions |


This is a living plan — [request a feature](https://github.com/CarbonKite/OneManCompany/issues) or [contribute directly](https://github.com/CarbonKite/OneManCompany/pulls).

---

## Documentation


| Document                               | Description                                                    |
| -------------------------------------- | -------------------------------------------------------------- |
| [Architecture](docs/architecture.md)   | System architecture, diagrams, module index, design philosophy |
| [Vessel System](docs/vessel-system.md) | Vessel + Talent deep dive, Harness protocols                   |
| [Task System](docs/task-system.md)     | Task status state machine                                      |
| [Coding Guide](vibe-coding-guide.md)   | Coding guidelines, testing rules, code style                   |
| [Changelog](CHANGELOG.md)              | Release history                                                |


---

## Community & Contributing

- **Build Talents** — Create new AI employee types for the Talent Market
- **Build Tools** — Add integrations (APIs, services, platforms)
- **Add Company Features** — Performance dashboards, OKR tracking, employee training...
- **Improve the OS** — Core engine, frontend, documentation
- **Share Demos** — Show what your AI company can build
- **Report Issues** — Help us find and fix bugs

See [vibe-coding-guide.md](vibe-coding-guide.md) for coding guidelines.

---

## Citation

If you use Memento-OneManCompany in your research or project, please cite it:

```bibtex
@software{onemancompany2025,
  title = {Memento-OneManCompany: The AI Operating System for One-Person Companies},
  author = {Zhengxu Yu, Fu Yu, Zhiyuan He, Yuxuan Huang, Weilin Luo, Jun Wang},
  url = {https://github.com/CarbonKite/OneManCompany},
  year = {2025},
  license = {Apache-2.0}
}
```

---

## Links

<p>
  <a href="https://carbonkites.com">
    <img src="img/talent-market-icon.png" alt="Talent Market" height="28" style="vertical-align: middle;" />
    <b>Talent Market</b>
  </a>
  &nbsp;—&nbsp;Community-verified AI employee marketplace
</p>

<!-- Add more links here, same format:
<p>
  <a href="https://your-url.com">
    <img src="img/your-icon.svg" alt="Name" height="28" style="vertical-align: middle;" />
    <b>Project Name</b>
  </a>
  &nbsp;—&nbsp;Short description
</p>
-->

---

## License

[Apache License 2.0](LICENSE) — Free for commercial use and modification, with attribution required.