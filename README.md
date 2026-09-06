# 🧠 Jarvis for Hermes Agent (v5.0.0)

Jarvis is a **native Hermes Agent plugin**. Hermes remains the primary execution platform; Jarvis adds organisational intelligence, long-term experience, workforce routing, contextual memory, and guarded self-evolution.

Jarvis owns the local TencentDB Agent Memory runtime used by its memory layer. It does not require a separate Jarvis application or `jarvis-server` MCP daemon.

## Native Hermes architecture

```text
USER
  ↓
HERMES APP / AIAgent
  ↓
JARVIS NATIVE PLUGIN
  ├── goal classification
  ├── workforce intelligence
  ├── organisational experience
  ├── Jarvis memory provider
  └── owned local runtime
       ├── Ollama (only if Jarvis started it)
       └── Tencent memory-core / memory-hub / proxy
  ↓
HERMES NATIVE EXECUTION
  ├── Bots / profiles
  ├── temporary subagents
  ├── Kanban / workflows
  ├── skills
  └── Hermes tools
```

Jarvis does not create a second model loop, tool runtime, Bot framework, or Kanban system. Hermes continues to own those capabilities.

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

When the `hermes` command is available, the source installer attempts to enable Jarvis and select `memory.provider=jarvis`. The Hermes-facing administrator skill is provided at `hermes-skill/SKILL.md`.

## Start and stop Jarvis

The primary operator commands are:

```bash
hermes start jarvis
hermes stop jarvis
```

`hermes start jarvis` is the master switch for the Jarvis runtime:

```text
hermes start jarvis
        ↓
reuse or provision pinned TencentDB source
        ↓
check Ollama :11434
        ├── already running → reuse it
        └── not running → start it and record Jarvis ownership
        ↓
ensure qwen3.5:4b exists
        ↓
configure Tencent MEMORY_LLM_* and PROXY_* → Ollama
        ↓
start memory-core
        ↓
start memory-hub
        ↓
start proxy
        ↓
install/load macOS launchd supervisor (macOS only)
```

`hermes stop jarvis` unloads the macOS supervisor, stops TencentDB when Jarvis owns it, and stops **only the Ollama server process Jarvis started**. A pre-existing Ollama server is left running.

The lifecycle state is persisted under:

```text
~/.hermes/.jarvis/runtime-state.json
```

This state is required because Hermes CLI commands and the long-running macOS supervisor are separate processes.

## Automatic connection to Hermes

After Jarvis is installed and enabled, normal Hermes sessions load the native plugin. Jarvis supplies intelligence and memory context to Hermes' existing agent loop while Hermes continues to run the actual Bots, subagents, Kanban/workflows, skills, and tools.

After `hermes stop jarvis`, the persisted disabled state prevents normal session hooks from restarting the local runtime. `hermes start jarvis` re-enables it.

## TencentDB + Ollama

TencentDB Agent Memory is carried as a pinned repository submodule and is also maintained in a persistent runtime checkout:

```text
vendor/TencentDB-Agent-Memory
~/.hermes/.jarvis/tencentdb/source
```

Pinned Tencent revision:

```text
439f22ace03a08de828597b4eea2661f0978510c
```

Jarvis does not reinstall TencentDB on every start when the persistent checkout is already present.

### Automatic Ollama configuration

Jarvis automatically configures the supported TencentDB LLM/upstream settings to use the local Ollama OpenAI-compatible endpoint:

```text
MEMORY_LLM_BASE_URL=http://host.docker.internal:11434/v1
MEMORY_LLM_API_KEY=ollama
MEMORY_LLM_MODEL=qwen3.5:4b
MEMORY_LLM_PROTOCOL=openai

PROXY_UPSTREAM_URL=http://host.docker.internal:11434/v1
PROXY_UPSTREAM_API_KEY=ollama
PROXY_UPSTREAM_MODEL=qwen3.5:4b
```

On startup Jarvis runs `ollama show qwen3.5:4b`; when the model is missing it runs `ollama pull qwen3.5:4b`. Existing models are not downloaded again.

Model files remain installed after `hermes stop jarvis`. The `qwen3.5:4b` model here is for Jarvis/TencentDB memory services; it does **not** replace Hermes' primary AIAgent model.

## macOS launchd watchdog

Jarvis now has a real macOS user-level `launchd` supervisor. This is intentionally different from a Linux `systemd` service or a Python watchdog thread.

When Jarvis starts on macOS, it installs/loads:

```text
~/Library/LaunchAgents/com.jarvis.hermes-runtime.plist
```

The supervisor runs independently of the Hermes CLI process and performs a health check every ~20 seconds:

```text
macOS launchd
      ↓
Jarvis supervisor
      ↓
health checks
  ├── Ollama :11434
  ├── memory-core :8420
  ├── memory-hub :8125
  └── proxy :8096
      ↓
if unhealthy → Jarvis runtime recovery
```

`launchd` also restarts the supervisor itself if the supervisor process crashes.

### Ownership and recovery rules

The supervisor must never take ownership of an unrelated Ollama process. If Ollama was already running when Jarvis started, Jarvis records that it does not own that server and does not kill it during stop/recovery.

If Jarvis itself started Ollama and that process later dies, the supervisor can restart it and re-establish the required TencentDB connection.

If one or more TencentDB services become unreachable, the supervisor asks Jarvis' runtime to recover the stack using Tencent's supported startup scripts. Persistent TencentDB volumes and Jarvis experience data are preserved.

### Explicit stop remains authoritative

```bash
hermes stop jarvis
```

unloads the `launchd` service first, disables the persisted runtime state, and then stops only Jarvis-owned services. This prevents the supervisor from immediately bringing the stack back after an intentional shutdown.

### Manual watchdog status

The supervisor can be inspected on macOS through its native label:

```bash
launchctl print gui/$(id -u)/com.jarvis.hermes-runtime
```

Jarvis also exposes `/jarvis status` and the native `jarvis_runtime` status tool.

### Important macOS limitation

The watchdog is deliberately a **user-level** launch agent. It supervises the Jarvis runtime while the user session is active. It is not a system-wide root daemon and does not replace Hermes' own watchdog/lifecycle mechanisms.

## In-Hermes controls

```text
/jarvis status
/jarvis start
/jarvis stop
```

Native tools:

```text
jarvis_orchestrate
jarvis_record_outcome
jarvis_runtime
```

## Runtime opt-out

To prevent automatic local service startup during normal Hermes hooks:

```bash
export JARVIS_TENCENT_AUTOSTART=0
```

The explicit `hermes start jarvis` command remains the operator control and forces the runtime on.

On macOS, using `hermes start jarvis` still enables the launchd supervisor because that is an explicit operator action.

## Plug out

Non-destructive removal:

```bash
hermes stop jarvis
hermes config set memory.provider ""
hermes plugins disable jarvis
hermes plugins remove jarvis
```

Persistent Jarvis experience data and TencentDB data volumes are intentionally retained. Do not delete those stores unless the user explicitly requests destructive data removal.

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

Recalled memory is untrusted evidence, not executable instructions. Credentials/private keys are not intentionally captured into experience memory. Jarvis does not bypass Hermes' permission, tool, or policy boundaries.
