# 🧠 Jarvis for Hermes Agent (v1.0.0)

Jarvis is a native Hermes extension that turns Hermes into a larger self-improving AI-worker system. Hermes remains the execution platform and Jarvis becomes the organisational brain, long-term memory provider, experience engine and guarded self-evolution layer.

The target capability set combines the strongest ideas of Hermes, Lemon-style experience-driven evolution and OpenWorker-style deliverable-oriented autonomous work, while using Hermes' existing runtime instead of creating a second agent framework.

## Combined architecture

```text
USER
  ↓
HERMES APP / AIAgent
  ↓
JARVIS
  ├── goal classification
  ├── workforce intelligence
  ├── experience retrieval
  ├── knowledge retrieval
  ├── strategy selection
  ├── self-evolution proposals
  └── memory provider
  ↓
HERMES NATIVE EXECUTION
  ├── Bots / profiles
  ├── temporary subagents
  ├── Kanban
  ├── skills
  ├── tools / browser / terminal / filesystem
  └── cron / automation
  ↓
DELIVERABLE / RESULT
  ↓
JARVIS LEARNS
  ├── outcome
  ├── failures
  ├── lessons
  ├── Bot performance
  ├── strategy effectiveness
  └── reusable experience
  ↓
BETTER NEXT EXECUTION
```

## Responsibility split

### Hermes owns

- UI and conversation.
- The AIAgent loop and model calls.
- Tools and tool execution.
- Bots/profiles and their native identity, skills and sessions.
- Temporary subagents.
- Kanban and worker processes.
- Cron and scheduling primitives.
- Actual file, terminal, browser and connector operations.

### Jarvis owns

- Goal understanding and simple/moderate/complex routing.
- Workforce discovery and capability/performance scoring.
- Cross-Bot organisational knowledge.
- Long-term memory retrieval and contextualisation.
- Experience records: what worked, what failed and why.
- Strategy learning and guarded self-evolution.
- Deliverable awareness and verification recommendations.
- Tencent MemoryCore/TencentDB integration.

Jarvis does **not** create a second Kanban system, tool runtime, Bot framework or model loop.

## Simple work

Short requests remain on the normal Hermes path. Jarvis deliberately stays quiet for trivial prompts so it does not add unnecessary context or latency.

## Complex work

For multi-step, recurring or deliverable-oriented goals, Jarvis analyses the goal and identifies relevant Hermes workers, previous experience and an appropriate strategy. Hermes then executes using its existing Bots, subagents, Kanban and tools.

Jarvis can expose `jarvis_orchestrate` for an explicit plan request, but natural-language work does not require the user to operate a separate Jarvis application.

## Jarvis as a native Hermes MemoryProvider

The package publishes the Hermes memory-provider entry point named `jarvis`. When selected, Jarvis participates in Hermes memory lifecycle events such as `prefetch`, `sync_turn`, `on_session_end`, `on_pre_compress`, `on_delegation` and `on_memory_write`.

The memory stack is:

```text
Hermes Bot/profile memory
          +
Jarvis experience / organisation memory
          +
Tencent MemoryCore semantic knowledge
```

TencentDB/MemoryCore is an internal Jarvis backend. It is not exposed as a separate Hermes memory provider, so the user configures and thinks about **Jarvis**, not TencentDB.

## Self-evolution

Jarvis records outcomes and uses repeated evidence to improve future routing and strategy recommendations:

```text
TASK
 ↓
EXECUTE
 ↓
VERIFY
 ↓
OUTCOME
 ↓
DIAGNOSE
 ↓
LESSON / EXPERIENCE
 ↓
POLICY PROPOSAL
 ↓
EVIDENCE CHECK
 ↓
KEEP / REJECT / ROLLBACK
 ↓
NEXT TASK
```

The current evolution layer is deliberately conservative: it versions policy evidence and does not silently rewrite Hermes source code.

## Installation

Python package installation into the **same Python environment used by Hermes** is the primary integration path:

```bash
pip install /path/to/jarvis-plugin-for-hermes-agent
```

Development mode:

```bash
pip install -e /path/to/jarvis-plugin-for-hermes-agent
```

The package publishes:

```text
hermes_agent.plugins
    jarvis = jarvis_memory.hermes_plugin:register

hermes_agent.memory_providers
    jarvis = jarvis_memory.hermes_memory_provider:provider_factory
```

For a directory-plugin installation, the repository also contains `hermes-plugin/jarvis/`.

Because Hermes Desktop distributions can package their own runtime, the package must be installed into the runtime that actually loads Hermes plugins. Jarvis cannot safely assume that the system Python is the Desktop application's Python.

## Tencent MemoryCore configuration

Tencent configuration stays inside Jarvis:

```bash
export TDAI_GATEWAY_URL=http://127.0.0.1:8420
export TDAI_GATEWAY_API_KEY=YOUR_KEY
export TDAI_GATEWAY_SERVICE_ID=default
export TDAI_TEAM_ID=default
export TDAI_AGENT_ID=default
export TDAI_API_VERSION=v3
```

Jarvis continues with local experience storage when MemoryCore is unavailable, using its circuit breaker rather than making Hermes execution fail.

## Project structure

```text
src/jarvis_memory/
├── hermes_plugin.py            # native Hermes general plugin + lifecycle hooks
├── hermes_memory_provider.py   # native Hermes MemoryProvider
├── intelligence.py             # routing, Bot scoring, context assembly
├── evolution.py                # evidence-gated self-evolution
├── experience_store.py         # durable local experience/policy state
├── tencent_memory.py           # Tencent MemoryCore client (Jarvis-owned)
├── core.py                     # existing memory facade + redaction
├── workflow_store.py           # legacy/compat durable Jarvis workflow store
├── gateway_supervisor.py       # legacy/compat Gateway supervision
├── orchestrator.py             # standalone bootstrap
├── orchestration/              # Bot/profile discovery and planning contracts
└── tools/                      # existing MCP/legacy workflow compatibility tools

hermes-plugin/jarvis/
├── plugin.yaml                 # directory-plugin manifest
└── __init__.py                 # adapter to jarvis_memory.hermes_plugin
```

## Compatibility

The MCP server and existing Jarvis workflow APIs remain available for compatibility with earlier integrations. New Hermes installations should prefer the native plugin + memory-provider entry points.

## Safety

Recalled memory is untrusted evidence, not executable instructions. Credentials/private keys are redacted before capture. Jarvis does not directly execute Hermes tools. High-impact changes should continue to use Hermes' existing approval/governance mechanisms.

## License

Proprietary / Commercial. All rights reserved.
