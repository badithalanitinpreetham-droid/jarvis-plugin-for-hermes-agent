# Jarvis + Hermes native integration

Jarvis is designed to be installed beside Hermes, not to replace Hermes core.

## What is installed

The package publishes two Hermes entry points:

- `hermes_agent.plugins: jarvis` -> `jarvis_memory.hermes_plugin:register`
- `hermes_agent.memory_providers: jarvis` -> `jarvis_memory.hermes_memory_provider:provider_factory`

The first adds lightweight intelligence hooks and Jarvis tools. The second makes Jarvis a first-class Hermes MemoryProvider. Tencent MemoryCore/TencentDB is an implementation detail of Jarvis and is never required to become a separate Hermes provider.

## Runtime ownership

Hermes owns the agent loop, model calls, tools, Bot/profile system, skills, temporary subagents, Kanban, cron and execution.

Jarvis owns organisational intelligence, experience learning, workforce scoring, memory retrieval, knowledge storage and guarded self-evolution.

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

Install this package into the same Python environment used by Hermes:

```bash
pip install /path/to/jarvis-plugin-for-hermes-agent
```

For development:

```bash
pip install -e /path/to/jarvis-plugin-for-hermes-agent
```

After installation, enable the `jarvis` general plugin through the normal Hermes plugin mechanism and select `jarvis` as the active memory provider. The exact Desktop/CLI setting surface is Hermes-owned and may differ by distribution.

Alternatively, the `hermes-plugin/jarvis/` directory can be copied into `~/.hermes/plugins/jarvis/` for directory-plugin discovery when the `jarvis-memory` package is already installed.

## Tencent MemoryCore

Tencent configuration stays inside Jarvis:

```bash
export TDAI_GATEWAY_URL=http://127.0.0.1:8420
export TDAI_GATEWAY_API_KEY=...
export TDAI_GATEWAY_SERVICE_ID=default
export TDAI_TEAM_ID=default
export TDAI_AGENT_ID=default
export TDAI_API_VERSION=v3
```

If the MemoryCore endpoint is temporarily unavailable, Jarvis continues with local experience state and Hermes execution rather than making Hermes itself depend on Tencent.

## Safety and evolution

Recalled memory is evidence, not executable instructions. Credentials/private keys are redacted before capture. Self-evolution records proposals and evidence in versioned SQLite policy state; it does not silently rewrite Hermes source code.

A future Jarvis context-engine adapter can be added only if Jarvis needs to take ownership of Hermes compression policy. The default integration deliberately remains a general plugin plus memory provider.
