# Execution Modes

Founding employees (EA, HR, COO, CSO) support two execution modes. You can switch between them in the browser settings panel.

## Company Hosted Agent

The default mode. OneManCompany's built-in agent framework handles everything.

- **How it works**: Tasks are executed by OMC's internal LangChain-based agent, calling LLMs through OpenRouter
- **Requirements**: OpenRouter API Key (configured during setup)
- **Best for**: Getting started quickly, lower cost, full control over model selection
- **Model flexibility**: Each employee can be assigned a different model in their profile

## Claude Code

Employees run as Claude Code CLI sessions, leveraging Anthropic's most capable coding agent.

- **Requirements**: [Claude Pro or Max subscription](https://claude.ai) + Claude Code CLI installed
- **Best for**: Complex coding tasks, stronger autonomous reasoning, lower token cost

!!! note "Subscription Required"
    Claude Code mode requires an active Claude Pro ($20/mo) or Max ($100/mo) subscription. The subscription is managed through your Anthropic account, separate from OpenRouter.

### Install Claude Code

1. Subscribe to [Claude Pro or Max](https://claude.ai)
2. Install the CLI:

    ```bash
    # macOS / Linux
    npm install -g @anthropic-ai/claude-code

    # Verify installation
    claude --version
    ```

3. Authenticate:

    ```bash
    claude auth login
    ```

For detailed instructions, see the [official Claude Code documentation](https://docs.anthropic.com/en/docs/claude-code).

## OpenClaw

[OpenClaw](https://github.com/anthropics/openclaw) is an open-source alternative that supports more LLM backends.

- **Requirements**: OpenClaw CLI installed + compatible LLM API key
- **Best for**: Using non-Anthropic models with Claude Code-level capabilities

### Install OpenClaw

1. Install the CLI:

    ```bash
    # macOS / Linux
    npm install -g openclaw

    # Verify installation
    openclaw --version
    ```

2. Configure your LLM provider:

    ```bash
    openclaw config set provider <your-provider>
    openclaw config set api-key <your-api-key>
    ```

For detailed instructions, see the [OpenClaw documentation](https://github.com/anthropics/openclaw).

## Switching Modes

1. Open the **Settings** panel in the browser
2. Select the employee you want to configure
3. Choose between **Company Hosted** and **Claude Code**
4. The change takes effect on the next task

## How They Compare

| | Company Hosted Agent | Claude Code | OpenClaw |
| --- | --- | --- | --- |
| **Cost model** | Pay-per-token via OpenRouter | Flat subscription fee | Depends on LLM provider |
| **Setup** | Just an API key | Claude CLI + subscription | OpenClaw CLI + API key |
| **Model choice** | Any model on OpenRouter | Claude (Anthropic) | Multiple LLM backends |
| **Coding ability** | Good (depends on model) | Excellent | Good to excellent |
| **Tool access** | OMC built-in tools | Full Claude Code + MCP tools | OpenClaw + MCP tools |
| **Best for** | General tasks, budget control | Complex dev work | Flexibility in model choice |

## Hybrid Setup

You can mix modes across employees. For example:

- **EA, HR, CSO** → Company Hosted (mostly communication and coordination tasks)
- **COO** → Claude Code (complex task breakdown and code review)
- **Engineers** → OpenClaw (if you prefer non-Anthropic models)

This lets you optimize cost and capability per role.
