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

- **How it works**: Each task spawns a Claude Code CLI session with MCP tools connecting back to the company
- **Requirements**: [Claude Pro or Max subscription](https://claude.ai) with Claude Code CLI installed
- **Best for**: Complex coding tasks, stronger autonomous reasoning, access to Claude's full tool ecosystem

!!! note "Subscription Required"
    Claude Code mode requires an active Claude Pro ($20/mo) or Max ($100/mo) subscription. The subscription is managed through your Anthropic account, separate from OpenRouter.

## Switching Modes

1. Open the **Settings** panel in the browser
2. Select the employee you want to configure
3. Choose between **Company Hosted** and **Claude Code**
4. The change takes effect on the next task

## How They Compare

| | Company Hosted Agent | Claude Code |
| --- | --- | --- |
| **Cost model** | Pay-per-token via OpenRouter | Flat subscription fee |
| **Setup** | Just an API key | Claude CLI + subscription |
| **Model choice** | Any model on OpenRouter | Claude (Anthropic) |
| **Coding ability** | Good (depends on model) | Excellent |
| **Tool access** | OMC built-in tools | Full Claude Code + MCP tools |
| **Best for** | General tasks, budget control | Complex dev work |

## Hybrid Setup

You can mix modes across employees. For example:

- **EA, HR, CSO** → Company Hosted (mostly communication and coordination tasks)
- **COO** → Claude Code (complex task breakdown and code review)

This lets you optimize cost and capability per role.
