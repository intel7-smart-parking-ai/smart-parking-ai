"""Enumerate runtime AST risk patterns without treating every match as a defect."""
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
rows = []
for file in [*sorted((ROOT/"chatbot").glob("*.py")), ROOT/"server_dev.py", ROOT/"server_fastapi.py"]:
    source = file.read_text(encoding="utf-8")
    tree = ast.parse(source)
    def add(node, kind, text):
        rows.append({"file": file.relative_to(ROOT).as_posix(), "line": node.lineno, "kind": kind, "code": text})
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                add(node, "bare_except", ast.get_source_segment(source, node).splitlines()[0])
            elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                add(node, "except_pass", ast.get_source_segment(source, node).splitlines()[0])
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
            add(node, "while_true", "while True")
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            keys = {k.arg for k in node.keywords}
            if name.endswith("urlopen") and "timeout" not in keys:
                add(node, "urlopen_without_timeout", name)
            if name.endswith(("read_text", "write_text")) and "encoding" not in keys:
                add(node, "text_without_encoding", name)
            if name == "open" and "encoding" not in keys and not any(isinstance(a, ast.Constant) and isinstance(a.value, str) and "b" in a.value for a in node.args[1:]):
                add(node, "text_without_encoding", name)
            if name.startswith("subprocess.") and name.split(".")[-1] in {"run","call","check_output","check_call","Popen"} and "timeout" not in keys:
                add(node, "subprocess_without_timeout", name)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            contexts = [ast.unparse(item.context_expr) for item in node.items]
            if any("lock" in x.lower() or "condition" in x.lower() for x in contexts):
                add(node, "lock_scope", ", ".join(contexts))
        if isinstance(node, ast.AsyncFunctionDef):
            add(node, "async_function", node.name)
print(json.dumps(rows, ensure_ascii=False, indent=2))
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
