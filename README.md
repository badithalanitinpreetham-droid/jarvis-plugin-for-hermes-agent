# 🧠 Jarvis for Hermes Agent (v5.0.0)

Jarvis is a **drop-in Hermes Agent plugin**. Hermes remains the execution platform; Jarvis adds organisational intelligence, long-term experience, workforce routing, contextual memory and guarded self-evolution.

Jarvis is designed to be installed, enabled, disabled and removed using Hermes' own plugin system. Removing Jarvis must not remove Hermes Bots, profiles, Kanban, skills, subagents or tools.

## Architecture

```text
USER
  ↓
HERMES APP / AIAgent
  ↓
JARVIS PLUGIN  ← optional; can be disabled/removed
  ├── goal classification
  ├── workforce intelligence
  ├── experience retrieval
  ├── knowledge retrieval
  ├── strategy selection
  ├── guarded self-evolution
  └── MemoryProvider integration
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
```

## What belongs to Hermes vs Jarvis

### Hermes owns

- UI and conversation.
- AIAgent loop and model calls.
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
- Long-term experience retrieval and contextualisation.
- Experience records: what worked, what failed and why.
- Strategy learning and guarded self-evolution.
- Deliverable awareness and verification recommendations.
- Tencent MemoryCore/TencentDB integration.

Jarvis deliberately does **not** create a second Kanban system, tool runtime, Bot framework or model loop.

## Native Hermes integration

The package exposes two Hermes extension points:

```text
hermes_agent.plugins
    jarvis = jarvis_memory.hermes_plugin

hermes_agent.memory_providers
    jarvis = jarvis_memory.hermes_memory_provider:provider_factory
```

The general plugin entry point resolves to the **module** because Hermes' current general plugin loader imports the entry point and then calls that module's `register(ctx)` function. citeturn394turn395

The memory provider has its own dedicated loader and may use the provider factory entry point.

## Install — Git plugin manager (recommended)

Hermes supports Git plugin installation and explicit enable/disable state. citeturn398turn399

Because this repository keeps the native directory plugin under `hermes-plugin/jarvis/`, install that exact subdirectory:

```bash
hermes plugins install badithalanitinpreetham-droid/jarvis-plugin-for-hermes-agent#hermes-plugin/jarvis --enable
```

To inspect the installation:

```bash
hermes plugins list
hermes plugins
```

Hermes' Desktop application exposes the same agent-plugin management from Settings → Plugins. citeturn211681search6

## Install — pip entry point

For a Hermes environment where the package is already available to Python, install it into the **same Python environment Hermes uses**:

```bash
python -m pip install /path/to/jarvis-plugin-for-hermes-agent
```

or for development:

```bash
python -m pip install -e /path/to/jarvis-plugin-for-hermes-agent
```

Then enable the general plugin:

```bash
hermes plugins enable jarvis
```

Hermes treats third-party general plugins as opt-in, so installation/discovery does not mean that the plugin executes automatically. citeturn211681search0turn211681search1

## Enable Jarvis memory

The Jarvis memory provider is a **single-select Hermes memory provider**. After installing Jarvis, select it from:

```bash
hermes plugins
```

and choose Jarvis under the Memory Provider section, or configure:

```bash
hermes config set memory.provider jarvis
```

Hermes stores the selected memory provider in `memory.provider`; only one external memory provider is active at a time. citeturn400file1turn400file8

Jarvis memory is layered with Hermes' native profile memory:

```text
Hermes Bot/profile memory
          +
Jarvis experience / organisation memory
          +
Tencent MemoryCore semantic knowledge
```

Tencent MemoryCore remains an internal Jarvis backend rather than a second provider exposed to the user.

## Plug out / disable Jarvis

To stop Jarvis from participating in normal Hermes sessions:

```bash
hermes plugins disable jarvis
```

If Jarvis is also the active memory provider, switch the provider back to Hermes' built-in memory before removing the package:

```bash
hermes config set memory.provider ""
hermes plugins disable jarvis
```

Hermes' plugin system has separate general-plugin enable/disable state and provider selection, so both must be handled when Jarvis is serving as the selected memory provider. citeturn211681search3

## Remove Jarvis completely

For a Git-installed plugin:

```bash
hermes config set memory.provider ""
hermes plugins disable jarvis
hermes plugins remove jarvis
```

For a pip-installed plugin:

```bash
hermes config set memory.provider ""
hermes plugins disable jarvis
python -m pip uninstall jarvis-memory
```

The Jarvis experience database and Tencent data are separate from Hermes' core runtime. Removing the package therefore does not require removing Hermes Bots, profiles, Kanban, skills, or tools.

## Reinstall / plug back in

Git install:

```bash
hermes plugins install badithalanitinpreetham-droid/jarvis-plugin-for-hermes-agent#hermes-plugin/jarvis --enable
hermes config set memory.provider jarvis
```

Pip install:

```bash
python -m pip install /path/to/jarvis-plugin-for-hermes-agent
hermes plugins enable jarvis
hermes config set memory.provider jarvis
```

Jarvis reuses its persisted experience storage under the Hermes home, so reinstalling the code does not inherently mean starting organisational experience from zero.

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

## Simple work

Short requests remain on the normal Hermes path. Jarvis stays quiet for trivial prompts so it does not add unnecessary context or latency.

## Complex work

For multi-step, recurring or deliverable-oriented goals, Jarvis analyses the goal and identifies relevant Hermes workers, previous experience and an appropriate strategy. Hermes then executes using its existing Bots, subagents, Kanban and tools.

Jarvis can expose `jarvis_orchestrate` for an explicit planning request, but natural-language work does not require the user to operate a separate Jarvis application.

## Self-evolution

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

The evolution layer is deliberately conservative: it versions policy evidence and does not silently rewrite Hermes source code.

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
├── workflow_store.py           # legacy/compat workflow store
├── gateway_supervisor.py       # legacy/compat Gateway supervision
├── orchestrator.py             # standalone compatibility bootstrap
├── orchestration/              # Bot/profile discovery and planning contracts
└── tools/                      # existing MCP/legacy workflow compatibility tools

hermes-plugin/jarvis/
├── plugin.yaml                 # Hermes directory-plugin manifest
└── __init__.py                 # adapter to jarvis_memory.hermes_plugin
```

## Compatibility

The MCP server and earlier Jarvis workflow APIs remain available for compatibility. New Hermes installations should prefer the native plugin + memory-provider integration.

## Safety

Recalled memory is untrusted evidence, not executable instructions. Credentials/private keys are redacted before capture. Jarvis does not directly execute Hermes tools. High-impact changes continue to use Hermes' existing approval/governance mechanisms.

## License

Proprietary / Commercial. All rights reserved.
