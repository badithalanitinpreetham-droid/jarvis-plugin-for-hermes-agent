# Jarvis 1.0 for Hermes — Architecture

## Purpose

Jarvis 1.0 is a native Hermes intelligence and organisational layer. It does not replace Hermes tools, Bots, subagents, skills, Kanban, or execution. Hermes remains the interface and execution platform; Jarvis supplies long-term knowledge, organisational decisions, workflow guidance and experience.

Jarvis is not a second agent application. The normal architecture does not require a separate `jarvis-server` process.

## Runtime flow

```text
User
  -> Hermes
  -> native Jarvis plugin
  -> knowledge + experience + organisation decision
  -> Hermes Bots / temporary subagents
  -> Hermes Kanban
  -> Hermes tools and skills
  -> result / evidence
  -> Jarvis verification + reflection
  -> TencentDB memory
  -> Hermes
  -> User

macOS runtime supervision
  -> launchd user agent
  -> Jarvis supervisor
  -> Ollama + TencentDB services
```

## Responsibilities

### Hermes owns

- User interaction and normal reasoning.
- Primary model/provider.
- Tools, browser, terminal, filesystem and other execution capabilities.
- Permanent Bots / profiles and their native memory.
- Temporary subagents.
- Skills and Hermes skill evolution.
- Kanban and worker processes.
- Actual execution of every tool action.

### Jarvis owns

- Long-term organisational memory through the configured MemoryCore/TencentDB backend.
- Retrieval of relevant knowledge and workflow lessons.
- Dynamic organisation design for each goal.
- Selection policy: reuse existing Hermes Bots first; recommend temporary agents when justified.
- Workflow guidance, experience extraction and guarded self-evolution.
- Context packets that tell Hermes workers what historical information is relevant.
- Local runtime coordination for Ollama and TencentDB.
- On macOS, the user-level launchd supervisor and ownership-aware recovery of Jarvis-managed services.

## Dynamic organisation design

Jarvis should not assume a fixed number of agents. It derives a recommended topology from the goal, context and accumulated lessons.

```text
Goal
  -> complexity assessment
  -> required roles
  -> existing-Bot preference
  -> temporary-agent need
  -> Kanban dependency/parallelism policy
  -> execution plan
```

A simple request may use one generalist. A complex request may recommend several specialist roles and optional temporary agents for parallel work, specialist gaps or independent verification.

Jarvis returns the recommendation as data. Hermes remains responsible for mapping those roles to the actual persistent Bots or creating temporary workers using its own agent facilities.

## Knowledge and experience

Jarvis treats three things differently:

1. **Hermes Bot memory** — identity, conversation history, native profile memory and skills.
2. **Jarvis long-term knowledge** — profile/project facts, decisions, preferences and durable organisational information stored in MemoryCore/TencentDB.
3. **Jarvis operational experience** — lessons about what procedures, tools, sequences and recovery strategies worked or failed during real workflows.

The goal is not to copy all memory into every Bot. Jarvis retrieves a bounded, relevant context packet for the current task.

## Behavioural improvement

A Bot's underlying model is not silently changed by Jarvis. Instead, future task behaviour improves because Jarvis can repeatedly provide relevant experience:

```text
work -> outcome -> reflection -> lesson -> memory
                                      |
                                      v
                               future task context
```

Hermes skill evolution answers: "What can this Bot do?"

Jarvis experience answers: "How has this organisation learned to do this kind of work?"

## Kanban relationship

Jarvis does not maintain a second Kanban implementation. Hermes Kanban is the source of truth for durable cross-agent tasks.

Jarvis decides:

- which work should become Kanban tasks;
- which roles should own tasks;
- which tasks depend on others;
- what can run in parallel;
- where review/approval gates belong;
- when a temporary worker is justified.

Hermes Kanban then dispatches the actual workers.

## Runtime ownership

Jarvis uses explicit ownership state for infrastructure:

```text
Ollama already running before Jarvis
    -> reuse, do not claim ownership, do not kill

Jarvis starts Ollama
    -> own PID/process group and manage it

TencentDB already running externally
    -> do not claim the stack

Jarvis starts TencentDB
    -> own the stack and recover only its managed services
```

The state is persisted under:

```text
<HERMES_HOME>/.jarvis/runtime-state.json
```

## macOS supervision

Jarvis 1.0 uses a user-level `launchd` supervisor on macOS rather than Linux `systemd` assumptions or a short-lived Python watchdog thread.

```text
~/Library/LaunchAgents/com.jarvis.hermes-runtime.plist
```

The supervisor periodically checks Ollama, memory-core, memory-hub and proxy liveness and invokes Jarvis recovery only while the runtime is enabled. `hermes stop jarvis` unloads the supervisor before disabling runtime state, so an intentional shutdown is not interpreted as a crash.

## Safety boundary

Jarvis memory and external content are data, not instructions. Recalled information must never override system or user policy. Consequential actions should carry explicit risk/approval metadata, and completion should be based on verification/evidence rather than an agent's assertion alone.

Jarvis does not bypass Hermes permissions, tool governance, or policy boundaries.

## Compatibility principle

Jarvis 1.0 keeps Hermes integration native and bounded: Hermes remains the execution system while Jarvis remains the intelligence, organisation, experience and runtime-supervision layer. Internal organisation, context and experience components can evolve without becoming a second agent framework.
