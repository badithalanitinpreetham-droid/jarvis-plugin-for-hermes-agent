with open("src/jarvis_memory/tools/autonomous.py", "r") as f:
    content = f.read()

target = """    def report_step_result(
        self,
        workflow_id: str,
        step_id: int,
        status: str,
        output: str,
        error: str = ""
    ) -> Dict[str, Any]:
        \"\"\"Record the result of a step and advance the workflow state.\"\"\""""

replacement = target + "\n        with self._lock:"

if target in content:
    idx = content.find(target) + len(target)
    lines = content[idx:].split('\n')
    
    end_idx = idx
    for line in lines:
        if line.strip() and not line.startswith("        ") and not line.startswith(" "):
            # Reached next method
            break
        end_idx += len(line) + 1
        
    method_body = content[idx:end_idx]
    
    indented_body = ""
    for line in method_body.split("\n"):
        if line:
            indented_body += "    " + line + "\n"
        else:
            indented_body += "\n"
            
    content = content[:idx] + "\n        with self._lock:\n" + indented_body + content[end_idx:]
    
    with open("src/jarvis_memory/tools/autonomous.py", "w") as f:
        f.write(content)
