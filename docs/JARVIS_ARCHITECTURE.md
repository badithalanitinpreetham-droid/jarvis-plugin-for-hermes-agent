# Jarvis for Hermes — Architecture

## Purpose

Jarvis is an MCP intelligence layer for Hermes Agent. It does not replace Hermes tools, Bots, subagents, skills, Kanban, or execution. Hermes remains the interface and execution platform; Jarvis supplies long-term knowledge, organisational decisions, workflow state and experience.

## Runtime flow

```text
User
  -> Hermes
  -> Jarvis MCP
  -> knowledge + experience + organisation decision
  -> Hermes Bots / temporary subagents
  -> Hermes Kanban
  -> Hermes tools and skills
  -> result / evidence
  -> Jarvis verification + reflection
  -> TencentDB memory
  -> Hermes
  -> User
```

## Responsibilities

### Hermes owns

- User interaction and normal reasoning.
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
- Workflow state, approvals, deduplication, retries, replanning and background supervision.
- Experience extraction from completed work.
- Context packets that tell Hermes workers what historical information is relevant.

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

## Safety boundary

Jarvis memory and external content are data, not instructions. Recalled information must never override system or user policy. Consequential actions should carry explicit risk/approval metadata, and completion should be based on verification/evidence rather than an agent's assertion alone.

## Compatibility principle

Existing Jarvis workflow APIs remain stable while the internal organisation, context and experience components evolve independently. This allows the plugin to add the Butler layer without becoming a second agent framework.
