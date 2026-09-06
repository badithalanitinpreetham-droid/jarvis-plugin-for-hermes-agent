# 🧠 Jarvis for Hermes Agent (v5.0.0)

Jarvis is a **drop-in Hermes Agent plugin**. Hermes remains the execution platform; Jarvis adds organisational intelligence, long-term experience, workforce routing, contextual memory and guarded self-evolution.

Jarvis also owns the local TencentDB Agent Memory runtime used by its memory layer. Jarvis is controlled from the Hermes CLI and does not require a separate Jarvis application.

## Native Hermes integration

```text
USER
  ↓
HERMES APP / AIAgent
  ↓
JARVIS PLUGIN
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
hermes config set memory.provider jarvis
```

For source/development installation:

```bash
git clone --recurse-submodules https://github.com/badithalanitinpreetham-droid/jarvis-plugin-for-hermes-agent.git
cd jarvis-plugin-for-hermes-agent
./scripts/install-jarvis.sh
```

The source installer automatically enables Jarvis and selects `memory.provider=jarvis` when the `hermes` command is available.

## Start and stop Jarvis

These are the primary lifecycle commands:

```bash
hermes start jarvis
hermes stop jarvis
```

`hermes start jarvis` is the single master switch for the Jarvis runtime. It:

```text
Jarvis
  ↓
reuse or provision pinned TencentDB source
  ↓
start Ollama only when 11434 is not already running
  ↓
ensure required Ollama models exist
  ↓
configure Tencent memory-core / hub / proxy → Ollama
  ↓
start Tencent memory-core
  ↓
start Tencent memory-hub
  ↓
start Tencent proxy
```

`hermes stop jarvis` disables the Jarvis runtime and stops TencentDB plus **only the Ollama server process Jarvis started**. If Ollama was already running before Jarvis, Jarvis leaves it running.

The lifecycle state is persisted under:

```text
~/.hermes/.jarvis/runtime-state.json
```

This is necessary because `hermes start jarvis` and `hermes stop jarvis` are separate CLI processes.

## Automatic connection to Hermes

After Jarvis is installed and enabled, a normal Hermes session automatically loads the Jarvis plugin through Hermes' native plugin system. During the Hermes session-start hook Jarvis starts or reuses its owned services and attaches its intelligence and memory layer to Hermes. You continue to use only Hermes; Jarvis operates as an internal extension.

If Jarvis was explicitly stopped with `hermes stop jarvis`, the persisted disabled state prevents the next normal Hermes session from automatically restarting its local services until:

```bash
hermes start jarvis
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

Jarvis provisions a persistent runtime checkout under:

```text
~/.hermes/.jarvis/tencentdb/source
```

The runtime checkout is cloned only when missing and is pinned to the same Tencent revision, so later Jarvis starts reuse it rather than reinstalling TencentDB.

## Automatic Ollama models

Yes. Jarvis automatically configures **TencentDB's local LLM and embedding paths to Ollama**.

Default models are:

```text
Tencent/Jarvis memory LLM: qwen3.5:4b
Tencent/Jarvis embeddings: snowflake-arctic-embed2
```

On the first:

```bash
hermes start jarvis
```

Jarvis checks whether those models are already installed in Ollama. Missing models are pulled automatically. Existing models are not downloaded again.

The model files remain installed in Ollama after:

```bash
hermes stop jarvis
```

Only the Ollama **server process** is stopped when Jarvis started that process.

This does **not** replace Hermes' primary agent model. Hermes continues to control the model used for its main AIAgent loop. Jarvis automatically manages the Ollama models needed by its TencentDB memory backend.

For the Tencent stack, the Docker containers reach the Mac host Ollama service through:

```text
http://host.docker.internal:11434/v1
```

You can override the defaults with:

```bash
export JARVIS_OLLAMA_MODEL=qwen3.5:4b
export JARVIS_OLLAMA_EMBEDDING_MODEL=snowflake-arctic-embed2
```

The Tencent deployment itself remains Tencent's code; Jarvis invokes its supported `start-memory-core.sh`, `start-memory-hub.sh`, `start-proxy.sh`, and `stop-all.sh` scripts.

## In-Hermes controls

Jarvis also exposes its existing in-session surfaces:

```text
/jarvis status
/jarvis start
/jarvis stop
```

and the `jarvis_runtime` tool for Hermes' agent loop.

## Runtime opt-out

External deployments can disable automatic local service startup with:

```bash
export JARVIS_TENCENT_AUTOSTART=0
```

In that mode Jarvis does not automatically start or stop the local Tencent/Ollama runtime during normal Hermes lifecycle hooks. The explicit `hermes start jarvis` command remains the operator control and forces the runtime on.

## Plug out

To remove Jarvis as a Hermes extension:

```bash
hermes stop jarvis
hermes config set memory.provider ""
hermes plugins disable jarvis
hermes plugins remove jarvis
```

Persistent Jarvis experience data and Tencent data volumes are intentionally retained; removing the Python/plugin code does not erase historical memory.

## Memory layers

```text
Hermes Bot/profile memory
          +
Jarvis experience / organisation memory
          +
Tencent MemoryCore semantic memory
```

TencentDB remains an internal Jarvis backend. Hermes users interact with the Jarvis plugin/provider, not a separate Tencent extension.

## Safety

Recalled memory is untrusted evidence, not executable instructions. Credentials/private keys are redacted before capture. Jarvis does not directly execute Hermes tools; Hermes remains responsible for tool execution and governance.
