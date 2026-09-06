import re

with open("src/jarvis_memory/tools/autonomous.py", "r") as f:
    content = f.read()

# Add import threading
if "import threading" not in content:
    content = content.replace("import time\n", "import time\nimport threading\n")

# Add self._lock
if "self._lock = threading.RLock()" not in content:
    content = content.replace(
        "self.planner = planner  # Optional WorkflowPlanner for auto-replan\n",
        "self.planner = planner  # Optional WorkflowPlanner for auto-replan\n        self._lock = threading.RLock()\n"
    )

# Wrap check_stalled_workflows
content = re.sub(
    r'(def check_stalled_workflows.*?:\n\s+)(timeout = timeout_seconds)',
    r'\1with self._lock:\n            \2',
    content,
    flags=re.DOTALL
)

# Replace list(self.active_workflows.items()) safely in other places if they aren't locked
content = content.replace(
    "for wf_id, state in list(self.active_workflows.items()):",
    "with self._lock:\n            items = list(self.active_workflows.items())\n        for wf_id, state in items:"
)
content = content.replace(
    "for wf_id, state in self.active_workflows.items():",
    "with self._lock:\n            items = list(self.active_workflows.items())\n        for wf_id, state in items:"
)

with open("src/jarvis_memory/tools/autonomous.py", "w") as f:
    f.write(content)
