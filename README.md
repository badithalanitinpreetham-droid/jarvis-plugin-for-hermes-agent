# 🧠 Jarvis Memory for Hermes Agent (v4.1.0)

Jarvis Memory is an MCP server for Hermes Agent that provides persistent memory, durable workflow state, approvals, scheduling, progress tracking and self-evolution.

## Installation

### Prerequisites
1. Python 3.10+
2. Ollama for the zero-config local bootstrap
3. Node.js 22.16+ for the MemoryCore Gateway
4. Git

```bash
pip install jarvis-memory
jarvis-server
```

The zero-config launcher starts Ollama, pulls the configured local models, pins the MemoryCore checkout to a known compatible revision, installs/builds the Gateway, and only then imports the MCP server. This avoids configuration-order races.

### Hermes

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "jarvis-server",
      "args": []
    }
  }
}
```

## Configuration

The MemoryCore client uses v3 by default and sends `team_id`, `agent_id`, `user_id` and `x-tdai-service-id` for isolation. Set `TDAI_GATEWAY_API_KEY` when the Gateway requires authentication. `TDAI_API_KEY` is retained as a backwards-compatible fallback.

For an LLM-backed workflow planner, configure an OpenAI-compatible endpoint:

```bash
export JARVIS_PLANNER_LLM_URL=http://127.0.0.1:11434/v1
export JARVIS_PLANNER_LLM_MODEL=qwen3.5:0.5b
export JARVIS_PLANNER_LLM_KEY=ollama-local
```

Without a planner endpoint, Jarvis uses a deterministic fallback plan. Hermes remains the component that actually executes the returned steps.

## Safety

High-risk or low-confidence actions can require explicit approval. Memory is profile-scoped using the configured MemoryCore team/agent/user identity. Race groups are treated as logical cancellation: Jarvis cannot terminate an already-running Hermes process, so Hermes should stop or ignore loser executions when its runtime supports cancellation.

Local workflow state is stored in SQLite. Terminal workflow history is eligible for retention cleanup, while dedupe records remain durable so successful publish-like actions are not replayed after cleanup.

## License

Proprietary / Commercial. All rights reserved.
