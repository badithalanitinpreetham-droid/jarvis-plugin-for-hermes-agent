with open("src/jarvis_memory/tencent_memory.py", "r") as f:
    content = f.read()

if "import threading" not in content:
    content = content.replace("import time\n", "import time\nimport threading\n")

if "self._circuit_lock" not in content:
    content = content.replace(
        "self._consecutive_failures = 0",
        "self._consecutive_failures = 0\n        self._circuit_lock = threading.Lock()"
    )

content = content.replace(
    "    def _check_circuit(self):",
    "    def _check_circuit(self):\n        with self._circuit_lock:"
)
content = content.replace(
    "    def _record_success(self):",
    "    def _record_success(self):\n        with self._circuit_lock:"
)
content = content.replace(
    "    def _record_failure(self):",
    "    def _record_failure(self):\n        with self._circuit_lock:"
)

# Indent the bodies of those 3 methods
for method in ["_check_circuit", "_record_success", "_record_failure"]:
    # Using regex to indent the block
    import re
    pattern = rf"(def {method}\(self\):\n        with self._circuit_lock:\n)([\s\S]*?)(?=\n    def)"
    match = re.search(pattern, content)
    if match:
        body = match.group(2)
        indented = "\n".join(["    " + line if line else line for line in body.split("\n")])
        content = content[:match.start()] + match.group(1) + indented + content[match.end():]

# JSONDecodeError (Defect 8)
content = content.replace(
    "            if resp.content:\n                return resp.json()\n            return {}",
    "            if resp.content:\n                try:\n                    return resp.json()\n                except Exception:\n                    return {\"raw_text\": resp.text}\n            return {}"
)

with open("src/jarvis_memory/tencent_memory.py", "w") as f:
    f.write(content)
