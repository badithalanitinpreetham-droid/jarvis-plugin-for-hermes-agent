import re
import os

def fix_file(filepath):
    with open(filepath, "r") as f:
        content = f.read()

    # In autonomous.py
    content = content.replace(
        "all_workflows = dict(self.active_workflows)",
        "with self._lock:\n            all_workflows = dict(self.active_workflows)"
    )
    content = content.replace(
        "pending = []\n        for wf_id, state in self.active_workflows.items():",
        "pending = []\n        with self._lock:\n            items = list(self.active_workflows.items())\n        for wf_id, state in items:"
    )
    
    with open(filepath, "w") as f:
        f.write(content)

fix_file("src/jarvis_memory/tools/autonomous.py")

with open("src/jarvis_memory/server.py", "r") as f:
    content = f.read()
    content = content.replace(
        "all_wfs = dict(autonomous_executor.active_workflows)",
        "with autonomous_executor._lock:\n                all_wfs = dict(autonomous_executor.active_workflows)"
    )
with open("src/jarvis_memory/server.py", "w") as f:
    f.write(content)
