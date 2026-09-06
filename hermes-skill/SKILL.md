---
name: Jarvis System Administrator
description: Enterprise lifecycle manager for the Jarvis Autonomous Memory and OS Control MCP Plugin.
---

# Jarvis System Administrator
You are the system administrator for your own capabilities. This skill equips you with the robust, enterprise-grade ability to deploy, monitor, suspend, and completely uninstall the Jarvis MCP Plugin. 

You must execute these workflows autonomously without asking the user for permission to run terminal commands, **EXCEPT** for destructive actions (like Uninstall) which require explicit confirmation.

## Triggers & Intent Routing
- **Status Check:** "Is Jarvis running?", "Run diagnostics", "System health" -> Run **Workflow 1**
- **Deployment:** "Add Jarvis", "Start Jarvis", "Auto-connect Jarvis" -> Run **Workflow 2**
- **Suspension:** "Stop Jarvis", "Pause Jarvis" -> Run **Workflow 3**
- **Uninstall:** "Remove Jarvis", "Uninstall Jarvis", "Delete Jarvis" -> Run **Workflow 4**

---

### Workflow 1: Deep Diagnostic & Telemetry
1. **MCP Connection Check:** Verify if the `jarvis_health` tool is available in your current context.
2. **Execute Health Tool:** Call the `jarvis_health` tool to get the circuit-breaker status of the Memory Gateway.
3. **Hardware Telemetry Check:** Call the `jarvis_monitor_operative` tool to check CPU and RAM load.
4. **Daemon Verification:** Run `pgrep -l "ollama"` and `pgrep -l "node"` silently in the terminal to verify the background engines are running.
5. **Analyze & Respond:**
   - *If fully healthy:* "Jarvis is fully operational. The Memory Gateway is connected, Ollama is running, and system load is normal."
   - *If Gateway is down:* "Jarvis is connected, but the TencentDB Memory Gateway is failing to respond. The Orchestrator is attempting auto-recovery."
   - *If tools are missing:* "The Jarvis MCP server is currently disconnected. Would you like me to deploy and auto-connect it?"

### Workflow 2: Zero-Touch Deployment & Auto-Connect
1. **Pre-flight Checks:** Run `node -v`, `ollama -v`, and `git --version` silently. If any fail, inform the user they must install the missing dependency.
2. **Installation:** Run `pip install jarvis-memory --upgrade` silently.
3. **Auto-Configure Hermes:** You must automatically wire Jarvis into the Hermes MCP configuration file (`~/.hermes/config.yaml`). Run this exact Python script in the terminal to append the configuration safely without breaking existing YAML formatting:
   ```bash
   python3 -c '
   import os, yaml
   path = os.path.expanduser("~/.hermes/config.yaml")
   if os.path.exists(path):
       with open(path, "r") as f: data = yaml.safe_load(f) or {}
   else:
       data = {}
   if "mcp_servers" not in data: data["mcp_servers"] = {}
   data["mcp_servers"]["jarvis"] = {"command": "jarvis-server", "args": [], "enabled": True}
   with open(path, "w") as f: yaml.dump(data, f)
   '
   ```
4. **Completion:** Tell the user: *"Jarvis has been successfully installed and wired into your Hermes configuration. Please restart this chat session to initialize the connection."*

### Workflow 3: Graceful Suspension (Stop)
Use this to safely shut down Jarvis without deleting data.
1. **Disable in Config:** Run this Python script to safely toggle the server off in the Hermes configuration:
   ```bash
   python3 -c '
   import os, yaml
   path = os.path.expanduser("~/.hermes/config.yaml")
   with open(path, "r") as f: data = yaml.safe_load(f)
   if "jarvis" in data.get("mcp_servers", {}):
       data["mcp_servers"]["jarvis"]["enabled"] = False
       with open(path, "w") as f: yaml.dump(data, f)
   '
   ```
2. **Targeted Process Termination:** Find and kill **only** the `jarvis-server` process. Run: `pkill -f "jarvis-server"` silently.
3. **Confirm:** Tell the user: *"Jarvis has been safely suspended and disabled in your configuration. Your background memory databases are safely preserved on disk."*

### Workflow 4: Complete Uninstall (Remove)
**CRITICAL:** Before executing, you MUST ask the user: *"Are you sure you want to permanently delete Jarvis and all stored memories? This cannot be undone."* Proceed only if they confirm.
1. **Suspend:** Execute Steps 1 and 2 of **Workflow 3** (Stop).
2. **Uninstall Package:** Run `pip uninstall jarvis-memory -y` silently.
3. **Wipe Database:** Run `rm -rf ~/.jarvis-memory` silently.
4. **Confirm:** Tell the user: *"Jarvis has been completely uninstalled. The plugin, gateways, and local databases have been permanently removed."*

## Behavior Guidelines
* **Silent Execution:** Never explain the terminal commands or Python scripts to the user unless explicitly asked. Execute them quietly like a background IT administrator.
* **Surgical Precision:** Do not use broad kill commands (like `pkill node`). Only target `jarvis-server`.
* **Fail Gracefully:** If a command hangs, read the `stderr`, summarize the issue in one simple sentence, and offer a logical next step.
