# 🧠 Jarvis for Hermes Agent (v5.0.0)

Jarvis is a **drop-in Hermes Agent plugin**. Hermes remains the execution platform; Jarvis adds organisational intelligence, long-term experience, workforce routing, contextual memory and guarded self-evolution.

Jarvis also owns the local TencentDB Agent Memory runtime used by its memory layer. When Jarvis starts, it starts the required local Ollama process (only when Jarvis itself started it) and TencentDB's `memory-core → memory-hub → proxy` stack. On clean Jarvis/Hermes shutdown, Jarvis stops that stack and the Ollama process it owns.

## Native Hermes integration

```text
USER
  ↓
HERMES APP / AIAgent
  ↓
JARVIS PLUGIN  ← optional
  ├── goal classification
  ├── workforce intelligence
  ├── organisational experience
  ├── Tencent MemoryCore memory
  └── owned local runtime
       ├── Ollama (only if Jarvis started it)
       └── Tencent memory-core / memory-hub / proxy
  ↓
HERMES NATIVE EXECUTION
  ├── Bots / profiles
  ├── temporary subagents
  ├── Kanban
  ├── skills
  └── Hermes tools
```

Jarvis does not create a second model loop, tool runtime, Bot framework, or Kanban system.

The package publishes the native Hermes extension points:

```text
hermes_agent.plugins
    jarvis = jarvis_memory.hermes_plugin

hermes_agent.memory_providers
    jarvis = jarvis_memory.hermes_memory_provider:provider_factory
```

## Install

Recommended Git plugin install:

```bash
hermes plugins install badithalanitinpreetham-droid/jarvis-plugin-for-hermes-agent#hermes-plugin/jarvis --enable
```

Then select Jarvis as Hermes' memory provider:

```bash
hermes config set memory.provider jarvis
```

For source/development installation:

```bash
git clone --recurse-submodules https://github.com/badithalanitinpreetham-droid/jarvis-plugin-for-hermes-agent.git
cd jarvis-plugin-for-hermes-agent
./scripts/install-jarvis.sh
```

## TencentDB + Ollama ownership

TencentDB Agent Memory is pinned as a repository submodule at:

```text
vendor/TencentDB-Agent-Memory
```

Pinned Tencent revision:

```text
439f22ace03a08de828597b4eea2661f0978510c
```

Jarvis provisions a persistent checkout under `~/.hermes/.jarvis/tencentdb/source` when needed, so the Tencent repository is cloned only once per Hermes home and reused on later Jarvis starts.

When Jarvis activates with the default runtime settings:

```text
Jarvis start
   ↓
ensure Tencent source
   ↓
start Ollama if 11434 is not already running
   ↓
start Tencent memory-core
   ↓
start Tencent memory-hub
   ↓
start Tencent proxy
   ↓
Jarvis memory available at local MemoryCore
```

For the Tencent stack, Jarvis uses Ollama's OpenAI-compatible endpoint by default:

```text
http://host.docker.internal:11434/v1
```

Set the local model with:

```bash
export JARVIS_OLLAMA_MODEL=qwen3.5:4b
```

The model is not silently downloaded. Install/pull the Ollama model once on your machine; Jarvis then owns the Ollama **server process lifecycle**. A pre-existing Ollama server is detected and is never killed by Jarvis.

The Tencent deployment itself remains Tencent's code; Jarvis invokes its supported `start-memory-core.sh`, `start-memory-hub.sh`, `start-proxy.sh`, and `stop-all.sh` scripts.

## Runtime controls

Jarvis exposes both a Hermes tool and `/jarvis` command:

```text
/jarvis status
/jarvis start
/jarvis stop
```

`jarvis_runtime` supports the same `status`, `start`, and `stop` actions.

Default automatic ownership can be disabled for external deployments with:

```bash
export JARVIS_TENCENT_AUTOSTART=0
```

In that mode Jarvis does not start or stop the local Tencent/Ollama runtime.

## Plug out

Switch away from Jarvis memory and disable the general plugin:

```bash
hermes config set memory.provider ""
hermes plugins disable jarvis
```

The Jarvis memory provider's shutdown path releases the Tencent/Ollama runtime it owns. A normal Hermes process shutdown also triggers Jarvis cleanup through its exit handler.

For complete removal:

```bash
hermes config set memory.provider ""
hermes plugins disable jarvis
hermes plugins remove jarvis
```

The persistent Jarvis experience database and Tencent data volumes are intentionally retained; removing the Python/package code does not erase historical memory.

## Reconnect

```bash
hermes plugins enable jarvis
hermes config set memory.provider jarvis
```

Jarvis reuses the persisted experience DB and Tencent data rather than starting the organisation's learning history from zero.

## Memory layers

```text
Hermes Bot/profile memory
          +
Jarvis experience / organisation memory
          +
Tencent MemoryCore semantic memory
```

TencentDB remains an internal Jarvis backend. Hermes users interact with the `jarvis` plugin/provider, not a separate Tencent extension.

## Safety

Recalled memory is untrusted evidence, not executable instructions. Credentials/private keys are redacted before capture. Jarvis does not directly execute Hermes tools; Hermes remains responsible for tool execution and governance.
