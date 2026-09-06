# Jarvis 1.0 + Hermes native integration

Jarvis 1.0 is designed to be installed beside Hermes, not to replace Hermes core.

## What is installed

The package publishes two Hermes entry points:

- `hermes_agent.plugins: jarvis` -> `jarvis_memory.hermes_plugin`
- `hermes_agent.memory_providers: jarvis` -> `jarvis_memory.hermes_memory_provider:provider_factory`

The first adds lightweight intelligence hooks, runtime lifecycle management, and Jarvis tools. The second makes Jarvis a first-class Hermes memory provider. Tencent MemoryCore/TencentDB is an implementation detail of Jarvis and is not exposed as a separate Hermes provider.

## Runtime ownership

Hermes owns the agent loop, primary model/provider, tools, Bot/profile system, skills, temporary subagents, Kanban/workflows, cron and execution.

Jarvis owns organisational intelligence, experience learning, workforce scoring, memory retrieval, knowledge storage, lifecycle coordination and guarded self-evolution.

On macOS, Jarvis also owns its user-level launchd supervisor and only the local runtime processes explicitly attributed to Jarvis ownership.

## Normal path

Short/simple requests should stay on the normal Hermes path. Jarvis intentionally returns no extra pre-LLM context for trivial prompts.

## Complex path

For multi-step or deliverable goals, Jarvis adds a bounded routing context describing relevant Hermes workers and organisational experience. Hermes remains responsible for actually dispatching Bots, subagents, Kanban tasks and tools.

## Memory path

Set `memory.provider: jarvis` in Hermes. The Jarvis provider uses:

1. Hermes profile identity and session lifecycle.
2. Jarvis local SQLite experience state under `<HERMES_HOME>/.jarvis/`.
3. Tencent MemoryCore through `TencentMemoryClient` for durable semantic memory when configured.

The provider supports Hermes memory lifecycle methods including `prefetch`, `sync_turn`, `on_session_end`, `on_pre_compress`, `on_delegation` and `on_memory_write`.

## Installation

Install Jarvis 1.0 into the same Python environment used by Hermes:

```bash
pip install /path/to/jarvis-plugin-for-hermes-agent
```

For development:

```bash
pip install -e /path/to/jarvis-plugin-for-hermes-agent
```

After installation, enable the `jarvis` general plugin through Hermes' normal plugin mechanism and select `jarvis` as the active memory provider. The exact Desktop/CLI setting surface is Hermes-owned and may differ by distribution.

Alternatively, the `hermes-plugin/jarvis/` directory can be copied into `~/.hermes/plugins/jarvis/` for directory-plugin discovery when the `jarvis-memory` package is already installed.

## Runtime lifecycle

The canonical operator commands are:

```bash
hermes start jarvis
hermes stop jarvis
```

`hermes start jarvis` provisions/reuses the pinned TencentDB source, ensures the Jarvis/Tencent Ollama model exists, configures TencentDB to use Ollama, starts only missing services, and on macOS installs/loads the user-level `launchd` supervisor.

`hermes stop jarvis` unloads that macOS supervisor, stops only Jarvis-owned services, preserves data, and leaves any pre-existing Ollama server running.

## Tencent MemoryCore and Ollama

The supported TencentDB LLM/upstream configuration used by Jarvis is:

```text
MEMORY_LLM_BASE_URL=http://host.docker.internal:11434/v1
MEMORY_LLM_API_KEY=ollama
MEMORY_LLM_MODEL=qwen3.5:4b
MEMORY_LLM_PROTOCOL=openai

PROXY_UPSTREAM_URL=http://host.docker.internal:11434/v1
PROXY_UPSTREAM_API_KEY=ollama
PROXY_UPSTREAM_MODEL=qwen3.5:4b
```

Jarvis checks `qwen3.5:4b` with `ollama show` and pulls it only when missing. This model is for Jarvis/TencentDB memory services; it does not replace Hermes' primary model/provider.

TencentDB configuration stays internal to Jarvis. The runtime source is pinned and reused from `<HERMES_HOME>/.jarvis/tencentdb/source`.

## macOS launchd

Jarvis 1.0 uses a native user-level launchd supervisor on macOS:

```text
~/Library/LaunchAgents/com.jarvis.hermes-runtime.plist
```

The supervisor performs periodic runtime health checks and recovers enabled Jarvis-owned services when they fail. `launchd` also restarts the supervisor if the supervisor process exits unexpectedly.

Ownership is preserved across CLI/supervisor process boundaries. Jarvis does not claim an Ollama or TencentDB stack that was already owned externally.

## Safety and evolution

Recalled memory is evidence, not executable instructions. Credentials/private keys are redacted before capture. Self-evolution records proposals and evidence in versioned SQLite policy state; it does not silently rewrite Hermes source code.

Jarvis does not bypass Hermes permission, tool, policy, or execution boundaries.
