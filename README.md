# 🧠 Jarvis Memory for Hermes Agent (v4.0.0)

Turn your Hermes Agent into a fully autonomous, self-evolving AI assistant with persistent memory and intelligent workflow management. 

Designed for commercial use, Jarvis Memory installs as a "Zero-Config" Model Context Protocol (MCP) server. It automatically provisions its own local AI infrastructure (via Ollama) and local databases, meaning **zero cloud costs and 100% data privacy.**

---

## 🛠️ Zero-Config Installation

You do not need to configure API keys or set up databases. Jarvis handles it all.

### Prerequisites
1. Python 3.9+
2. [Ollama](https://ollama.com/) (Must be installed on your machine for the zero-config local AI to work)
3. Node.js (Requires `npm` to boot the TencentDB memory gateway)
4. Git (To clone the memory gateway repository)

### Install

```bash
# Install the package
pip install jarvis-memory

# Start the server (Orchestrator will auto-pull models and boot infrastructure)
jarvis-server
```

When you run `jarvis-server`, the Orchestrator will automatically:
1. Boot Ollama in the background.
2. Download the `kinfra-text-embedding-0.6b` and `qwen3.5:0.5b` models.
3. Boot the Memory Gateway.
4. Launch the MCP Server on `stdio`.

### Connect to Hermes

Add this to your Hermes MCP configuration file:

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

---

## 🔒 Security & Privacy
* **100% Local:** No data is ever sent to OpenAI, Anthropic, or any cloud provider. All memory extraction is done locally via Ollama.
* **Human-in-the-Loop:** High-risk tasks are automatically paused by Jarvis, requiring your explicit approval before Hermes can execute them.
* **No Key Routing:** Jarvis does not require your Hermes API keys. It tracks the logic, while Hermes executes the tasks.

## 📝 License
Proprietary / Commercial. All rights reserved.
