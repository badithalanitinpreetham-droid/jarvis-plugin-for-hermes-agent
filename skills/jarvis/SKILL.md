# Jarvis Runtime Skill for Hermes

## Purpose

Jarvis is an internal Hermes plugin that adds organisational intelligence, experience memory, routing guidance and self-evolution while Hermes remains responsible for the model loop, Bots, profiles, subagents, Kanban, skills and tool execution.

## Runtime lifecycle

The operator-facing master switch is:

```bash
hermes start jarvis
hermes stop jarvis
```

Do not instruct the user to run separate `ollama serve`, Tencent `start-all.sh`, or Jarvis server commands during normal operation.

When Jarvis is started, its runtime should:

1. Reuse or provision the pinned TencentDB Agent Memory source under `~/.hermes/.jarvis/tencentdb/source`.
2. Detect whether Ollama is already listening on `127.0.0.1:11434`.
3. Start Ollama only when it is not already running.
4. Ensure the configured Ollama model exists. The default is `qwen3.5:4b`; missing models are pulled automatically.
5. Generate TencentDB's supported `.env` values so `MEMORY_LLM_*` and `PROXY_*` point to the host Ollama OpenAI-compatible endpoint.
6. Start TencentDB `memory-core`, then `memory-hub`, then `proxy`.
7. Verify the expected local service ports before reporting success.

When Jarvis is stopped:

1. Stop TencentDB only when persisted Jarvis ownership says Jarvis started/owns that stack.
2. Stop Ollama only when persisted Jarvis ownership says Jarvis started it.
3. Never kill a pre-existing Ollama process merely because it is reachable.
4. Retain model files, Jarvis experience data and Tencent data volumes.

## Ollama model configuration

TencentDB's current supported deployment uses:

```text
MEMORY_LLM_BASE_URL
MEMORY_LLM_API_KEY
MEMORY_LLM_MODEL
MEMORY_LLM_PROTOCOL

PROXY_UPSTREAM_URL
PROXY_UPSTREAM_API_KEY
PROXY_UPSTREAM_MODEL
```

Jarvis maps both TencentDB LLM groups to Ollama by default. Do not invent separate `EMBEDDING_*` TencentDB settings unless the pinned Tencent revision explicitly introduces them.

The default endpoint for Docker containers on macOS is:

```text
http://host.docker.internal:11434/v1
```

The default Jarvis/Tencent model is:

```text
qwen3.5:4b
```

The model must exist in Ollama. Jarvis performs an `ollama show <model>` check and runs `ollama pull <model>` only when missing.

This model is for Jarvis/TencentDB memory services. It does not silently replace Hermes' primary AIAgent model.

## macOS runtime and watchdog guidance

macOS is not Linux. Do not depend on Linux-specific process-manager behaviour such as `systemd` or `prctl` for Jarvis lifecycle control.

The current process ownership implementation uses a dedicated process group (`start_new_session=True`) for Ollama, then terminates that group by persisted PID when Jarvis owns it. This works for a normal macOS user process, but a future always-on watchdog should use a native macOS `launchd` user agent rather than a long-lived Python thread launched from `hermes start jarvis`.

Recommended macOS watchdog architecture for a later enhancement:

```text
launchd user agent
      ↓
Jarvis supervisor
      ├── Ollama
      └── TencentDB containers
```

`launchd` should restart the supervisor after crashes and stop it cleanly when `hermes stop jarvis` removes/disables the service. Hermes' own startup watchdog remains separate; Jarvis should not duplicate Hermes' application watchdog.

## Hermes integration rules

Use Hermes native plugin surfaces. Jarvis may register:

- lifecycle hooks for session integration
- `jarvis_orchestrate` and `jarvis_record_outcome` tools
- the `jarvis_runtime` tool
- the `/jarvis` in-session command
- the CLI lifecycle exposed as `hermes start jarvis` and `hermes stop jarvis`

Jarvis must not create a second Hermes agent loop, tool executor, Bot registry, Kanban system, or independent user interface.

## Failure handling

A startup failure must not leave a half-started Jarvis stack. Clean up any Tencent services or Ollama process that Jarvis successfully started before the failing step, then return a clear error and keep persistent ownership state consistent.

Health checks should distinguish:

```text
Ollama reachable
memory-core reachable
memory-hub reachable
proxy reachable
```

A TCP port being open is a basic liveness signal, not proof that the service is semantically healthy. Future watchdog work should add lightweight HTTP health endpoints where the pinned Tencent revision exposes them.

## Operator status

Use:

```bash
hermes start jarvis
hermes stop jarvis
```

For diagnostics, the plugin can expose `/jarvis status` and the `jarvis_runtime` tool. Runtime logs live under:

```text
~/.hermes/.jarvis/logs/
```
