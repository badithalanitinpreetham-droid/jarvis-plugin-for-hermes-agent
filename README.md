# 🧠 Jarvis for Hermes Agent (v1.0)

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

## Hermes skill package

This repository provides a dedicated Hermes-facing administrator skill at:

```text
hermes-skill/SKILL.md
```

Use that skill when Hermes needs to reason about Jarvis installation, start/stop lifecycle, diagnostics, ownership, TencentDB/Ollama configuration, macOS `launchd` supervision, recovery, and safe removal. It describes Jarvis as a native Hermes extension and explicitly prevents Hermes from treating `jarvis-server` as the normal architecture.

A second implementation-oriented runtime skill is available at:

```text
skills/jarvis/SKILL.md
```

The two skills are intentionally aligned: `hermes-skill/SKILL.md` is the Hermes operator/administrator guidance, while `skills/jarvis/SKILL.md` is the runtime integration guidance maintained with the plugin code.

## Version 1.0

Jarvis **1.0** is the first public product release identity for this native Hermes integration. The Python package uses semantic package version `1.0.0`, while the user-facing product and plugin identity is `1.0`.

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

When the `hermes` command is available, the source installer enables Jarvis and selects `memory.provider=jarvis` on a best-effort basis. It also installs a user-level command shim for the requested `hermes start jarvis` / `hermes stop jarvis` syntax.

## Start and stop Jarvis

Hermes' plugin API is plugin-scoped, so the **native command form** is:

```bash
hermes jarvis start
hermes jarvis stop
```

The source installer additionally provides the requested compatibility form:

```bash
hermes start jarvis
hermes stop jarvis
```

The compatibility form is a thin front-end installed at `~/.hermes/bin/hermes`. It translates only these two Jarvis lifecycle commands into the native plugin form and passes every other Hermes command unchanged to the real Hermes executable. It never overwrites the real Hermes executable.

`hermes start jarvis` is therefore a convenience compatibility entry point, while `hermes jarvis start` is the direct Hermes-plugin command.

The lifecycle flow is:

```text
hermes start jarvis
        ↓
compatibility shim
        ↓
hermes jarvis start
        ↓
Jarvis plugin runtime
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
```

Only Jarvis-owned processes are eligible for recovery. The ownership state is persisted so a pre-existing Ollama server is not killed or silently replaced.

## Status

Use the native plugin command:

```bash
hermes jarvis status
```

The slash command is also available inside Hermes:

```text
/jarvis status
```

The runtime reports reachability and ownership for Ollama and the TencentDB services.

## Runtime opt-out

To keep Jarvis installed but stop its local services:

```bash
hermes jarvis stop
```

The compatibility equivalent is:

```bash
hermes stop jarvis
```

Stopping Jarvis does not stop Hermes itself. It also does not delete Ollama model files, TencentDB data volumes, or Jarvis experience data.

## Removal

Use the repository removal script:

```bash
./scripts/remove-jarvis.sh
```

The removal script first attempts to stop Jarvis and unload its launchd supervisor, clears the selected Jarvis memory provider, disables the plugin, uninstalls the Python package, and removes the Jarvis compatibility shim. Persistent Ollama models, TencentDB volumes, and Jarvis experience data are deliberately retained.

## Verification

Run:

```bash
hermes plugins doctor jarvis --ci
hermes plugins list
hermes jarvis status
```

Then exercise the lifecycle:

```bash
hermes jarvis start
hermes jarvis status
hermes jarvis stop
```

After a fresh shell (so `~/.hermes/bin` is on PATH), also verify the requested compatibility form:

```bash
hermes start jarvis
hermes stop jarvis
```
