# 🧠 Jarvis for Hermes Agent (v4.3.0)

Jarvis is an MCP intelligence and experience layer for Hermes Agent. It keeps Hermes as the user interface, model reasoning, tool runner, Bot/profile system, subagent system, skill-evolution system and Kanban owner. Jarvis adds long-term organisational knowledge, dynamic work organisation, durable workflow state, recovery and experience-driven context.

## Architecture

```text
YOU
  ↓
HERMES APP
  ↓
JARVIS MCP
  ├── Hermes Registry (read-only Bot/profile discovery)
  ├── TencentDB / MemoryCore long-term knowledge
  ├── experience and workflow lessons
  ├── dynamic Bot/agent organisation
  ├── bounded context broker
  └── durable workflow/recovery state
  ↓
HERMES BOTS + TEMPORARY SUBAGENTS
  ↓
HERMES KANBAN
  ↓
HERMES TOOLS + SKILLS
  ↓
RESULTS / EVIDENCE
  ↓
JARVIS VERIFY → REFLECT → LEARN
  ↓
TENCENTDB
  ↓
HERMES
  ↓
YOU
```

Jarvis does **not** create a second tool framework, Kanban system, chatbot UI, or execution runtime.

## What Hermes owns

- User conversation and model reasoning.
- Tools, browser, terminal, filesystem and other execution capabilities.
- Permanent Bots / profiles and their native memory.
- Temporary subagents.
- Skills and Hermes skill evolution.
- Kanban, task dispatch and worker processes.
- Actual tool execution.

## What Jarvis adds

### Hermes Bot/profile registry

Jarvis discovers Hermes' configured profiles from `HERMES_HOME` (or the default `~/.hermes` layout) and treats that filesystem configuration as the source of truth for permanent workers.

The registry can safely expose bounded metadata such as:

- profile/Bot ID and display name;
- role and description;
- configured model and provider;
- declared capabilities and installed skills;
- configured toolsets;
- terminal working directory;
- bounded `SOUL.md` excerpt;
- active/default flags and configuration validity.

Credential files, authentication databases, sessions and memory stores are outside the registry boundary. Secret-looking keys are filtered before metadata enters Jarvis context. Discovery is cached and can be explicitly refreshed, so planning does not repeatedly scan Hermes' configuration tree.

The same registry feeds organisation decisions and the worker context packet, so Jarvis does not maintain a separate copy of the Hermes workforce.

### Dynamic organisation

Jarvis analyses each goal and recommends how Hermes should organise the work:

- how many roles are needed;
- which existing permanent Bots should be preferred;
- when temporary subagents are useful;
- which work can run in parallel;
- which work depends on earlier work;
- where verification, review and approval belong;
- which workflow should be persisted.

The recommendation is returned as structured data for Hermes. Hermes remains responsible for selecting the real Bot/profile or creating temporary workers using its own facilities.

### Long-term knowledge

The configured MemoryCore/TencentDB backend stores durable profile, project and organisational knowledge. Jarvis retrieves only relevant bounded context for the current task rather than dumping all memory into a Bot.

### Experience

Jarvis records operational experience separately from a Bot's native memory:

```text
work → outcome → reflection → lesson → future context
```

Examples include successful procedures, recurring failures, useful recovery methods and project-specific operating patterns.

### Context broker

Before a Bot starts work, Jarvis can provide a bounded context packet containing the goal, active profile metadata, known Hermes Bot roster, selected Bot metadata, project/task context and relevant operational lessons. Memory is treated as evidence rather than executable instructions.

### Durable workflows

Jarvis retains workflow persistence, approvals, dependency-aware dispatch, parallel/race coordination, deduplication, replanning, cancellation, progress, reflection, scheduled goals, stall detection, tool-health state and Gateway supervision.

Workflow failures are terminal state until a deliberate retry/replan path replaces them. Approval state is persisted and cannot be bypassed by a restart or repeated polling call.

## Hermes skill evolution + Jarvis experience

These systems are complementary:

```text
Hermes skill evolution
    = what the Bot can learn to do

Jarvis experience
    = how the organisation has learned to accomplish work
```

Together they give a permanent Bot both capability and accumulated operational context without replacing its Hermes profile or model.

## Kanban

Jarvis does not replace Hermes Kanban. Jarvis decides the work topology; Hermes Kanban remains the durable task board and worker coordination layer.

## Installation

### Prerequisites

1. Python 3.10+
2. Ollama for the zero-config local bootstrap
3. Node.js 22.16+ for the MemoryCore Gateway
4. Git

```bash
pip install jarvis-memory
jarvis-server
```

The launcher starts Ollama, pulls configured local models, prepares a compatible MemoryCore Gateway and then starts the MCP server.

### Hermes configuration

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "jarvis-server",
      "args": []
    }
  }
}
```

## Configuration

MemoryCore v3 is used by default with team/agent/user isolation and the configured service ID. Set `TDAI_GATEWAY_API_KEY` when the Gateway requires authentication; `TDAI_API_KEY` remains a compatibility fallback.

An optional OpenAI-compatible planner can be used locally or remotely:

```bash
export JARVIS_PLANNER_LLM_URL=http://127.0.0.1:11434/v1
export JARVIS_PLANNER_LLM_MODEL=qwen3.5:0.5b
export JARVIS_PLANNER_LLM_KEY=ollama-local
```

Without a planner endpoint, Jarvis still produces a deterministic organisation-aware fallback plan.

## Project structure

```text
src/jarvis_memory/
├── core.py                    # memory facade and safe data handling
├── config.py                  # runtime configuration
├── server.py                  # MCP boundary for Hermes
├── workflow_store.py          # durable SQLite workflow state
├── gateway_supervisor.py      # Gateway watchdog and scheduled supervision
├── orchestrator.py            # local bootstrap / process orchestration
├── tencent_memory.py          # MemoryCore/TencentDB client
│
├── orchestration/
│   ├── contracts.py           # shared plan validation contract
│   ├── registry.py            # safe Hermes Bot/profile discovery + cache
│   ├── organisation.py        # dynamic role / worker organisation
│   ├── context.py             # bounded context packet for Hermes workers
│   └── experience.py          # operational experience summarisation
│
└── tools/
    ├── planner.py             # planning + replanning
    ├── autonomous.py          # durable workflow state machine
    ├── progress.py            # progress / Mermaid / Kanban rendering
    ├── os_assistant.py        # macOS voice, telemetry and safe OS controls
    └── __init__.py

tests/
├── test_autonomous.py
├── test_hardening.py
├── test_progress.py
├── test_replan.py
├── test_tencent_memory.py
└── test_organization.py
```

## Safety

High-risk or low-confidence workflow steps can require approval. Jarvis does not treat recalled memory, tool output, web content or other external data as instructions. Credentials and private keys are filtered or redacted before memory/context capture. Jarvis never executes Hermes tools itself.

## License

Proprietary / Commercial. All rights reserved.
