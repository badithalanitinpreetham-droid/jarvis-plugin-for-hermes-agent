import re

def wrap_method(filepath, method_name):
    with open(filepath, "r") as f:
        content = f.read()

    pattern = rf"(def {method_name}\(.*?\)\s*(?:->\s*[^:]+)?:\n\s+(?:\"\"\"[\s\S]*?\"\"\"\n\s+)?)"
    
    match = re.search(pattern, content)
    if not match:
        print(f"Could not find {method_name}")
        return
        
    start_idx = match.end()
    
    # Find base indent
    lines = content[start_idx:].split('\n')
    base_indent = ""
    for line in lines:
        if line.strip():
            base_indent = line[:len(line) - len(line.lstrip())]
            break
            
    # Wrap rest of method
    end_idx = start_idx
    for line in lines:
        if line.strip() and not line.startswith(base_indent) and not line.startswith(" "):
            break
        end_idx += len(line) + 1
        
    method_body = content[start_idx:end_idx]
    
    # Indent body
    indented_body = ""
    for line in method_body.split("\n"):
        if line:
            indented_body += "    " + line + "\n"
        else:
            indented_body += "\n"
            
    new_content = content[:start_idx] + base_indent + "with self._lock:\n" + indented_body + content[end_idx:]
    
    with open(filepath, "w") as f:
        f.write(new_content)

methods = ["start_workflow", "get_next_step", "approve_step", "report_step_result", "_auto_replan", "cancel_workflow", "_status_response", "get_workflow_status", "reflect"]
for m in methods:
    wrap_method("src/jarvis_memory/tools/autonomous.py", m)

