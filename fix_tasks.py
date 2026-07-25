"""Run from Windows cmd: python fix_tasks.py  (fixes tasks.py from git HEAD)"""
import subprocess, py_compile, tempfile, os, sys

REPO = os.path.dirname(os.path.abspath(__file__))
REL  = 'superagent-django/apps/tasks/tasks.py'
OUT  = os.path.join(REPO, REL.replace('/', os.sep))

result = subprocess.run(['git', 'show', f'HEAD:{REL}'], cwd=REPO, capture_output=True)
if result.returncode != 0:
    print("ERROR:", result.stderr.decode()); sys.exit(1)

content = result.stdout.decode('utf-8')
print(f"Loaded from git HEAD. Lines: {len(content.splitlines())}")

# ── 1. Locate and replace ReadEmailTool, append ReadMultipleEmailsTool ─────
old_start = 'class ReadEmailTool(BaseTool):\n    name = "read_email"\n    description = "Fetch emails from Gmail.'
old_end   = '\n\nclass SearchEmailTool(BaseTool):'

s = content.find(old_start)
e = content.find(old_end, s)
if s < 0 or e < 0:
    print("ERROR: ReadEmailTool block not found"); sys.exit(1)

new_block = (
'class ReadEmailTool(BaseTool):\n'
'    name = "read_email"\n'
'    description = (\n'
'        "Fetch the most recent 1 email from Gmail. Always returns exactly 1 email \\u2014 no limit parameter. "\n'
'        "Use for: \'read my email\', \'last email\', \'check my email\', \'my email\'. "\n'
'        "For multiple emails use read_multiple_emails. "\n'
'        "Input JSON: {\\"filter\\": \\"-in:spam -in:trash\\"}. Returns {\\"emails\\":[...], \\"count\\":1}."\n'
'    )\n'
'    zone = ToolZone.GREEN\n'
'\n'
'    def __init__(self, workspace_id=None):\n'
'        self._workspace_id = workspace_id\n'
'\n'
'    def run(self, input_str: str) -> str:\n'
'        service = _build_gmail_service(self._workspace_id)\n'
'        if service:\n'
'            from core.tools.gmail.read_emails import ReadEmailsTool\n'
'            import json as _j\n'
'            try:\n'
'                data = _j.loads(input_str) if isinstance(input_str, str) else input_str\n'
'                filt = data.get("filter", "-in:spam -in:trash") if isinstance(data, dict) else "-in:spam -in:trash"\n'
'            except Exception:\n'
'                filt = "-in:spam -in:trash"\n'
'            result = ReadEmailsTool(gmail_service=service).run(_j.dumps({"limit": 1, "filter": filt}))\n'
'            try:\n'
'                data = _j.loads(result)\n'
'                if data.get("error") and any(\n'
'                    k in str(data["error"]).lower()\n'
'                    for k in ("401", "403", "invalid_grant", "token", "credential", "unauthorized", "expired")\n'
'                ):\n'
'                    return _j.dumps({\n'
'                        "error": "Gmail authentication failed. Your Gmail token has expired. Please go to Integrations \\u2192 disconnect Gmail \\u2192 reconnect it to get a fresh token.",\n'
'                        "emails": [], "count": 0,\n'
'                    })\n'
'            except Exception:\n'
'                pass\n'
'            _cache_emails(self._workspace_id, result)\n'
'            return result\n'
'        return json.dumps({\n'
'            "error": "Gmail is not connected. Please go to Integrations and connect your Gmail account first.",\n'
'            "emails": [], "count": 0,\n'
'        })\n'
'\n'
'    def to_schema(self):\n'
'        return {"type": "function", "function": {\n'
'            "name": self.name, "description": self.description,\n'
'            "parameters": {"type": "object",\n'
'                "properties": {\n'
'                    "filter": {"type": "string", "description": "Gmail search filter. Default: \'-in:spam -in:trash\'."},\n'
'                }},\n'
'        }}\n'
'\n'
'\n'
'class ReadMultipleEmailsTool(BaseTool):\n'
'    name = "read_multiple_emails"\n'
'    description = (\n'
'        "Fetch multiple emails from Gmail. Use ONLY when the user explicitly says a number: "\n'
'        "\'last 5 emails\', \'recent 3\', \'show me 10 emails\', "\n'
'        "or plural with no number like \'check my emails\' / \'read my emails\' (use limit=5). "\n'
'        "Do NOT use for \'read my email\' or \'last email\' \\u2014 use read_email for those. "\n'
'        "Input JSON: {\\"limit\\": 5, \\"filter\\": \\"-in:spam -in:trash\\"}. Returns {\\"emails\\":[...], \\"count\\":N}."\n'
'    )\n'
'    zone = ToolZone.GREEN\n'
'\n'
'    def __init__(self, workspace_id=None):\n'
'        self._workspace_id = workspace_id\n'
'\n'
'    def run(self, input_str: str) -> str:\n'
'        service = _build_gmail_service(self._workspace_id)\n'
'        if service:\n'
'            from core.tools.gmail.read_emails import ReadEmailsTool\n'
'            import json as _j\n'
'            result = ReadEmailsTool(gmail_service=service).run(input_str)\n'
'            try:\n'
'                data = _j.loads(result)\n'
'                if data.get("error") and any(\n'
'                    k in str(data["error"]).lower()\n'
'                    for k in ("401", "403", "invalid_grant", "token", "credential", "unauthorized", "expired")\n'
'                ):\n'
'                    return _j.dumps({\n'
'                        "error": "Gmail authentication failed. Your Gmail token has expired. Please go to Integrations \\u2192 disconnect Gmail \\u2192 reconnect it to get a fresh token.",\n'
'                        "emails": [], "count": 0,\n'
'                    })\n'
'            except Exception:\n'
'                pass\n'
'            _cache_emails(self._workspace_id, result)\n'
'            return result\n'
'        return json.dumps({\n'
'            "error": "Gmail is not connected. Please go to Integrations and connect your Gmail account first.",\n'
'            "emails": [], "count": 0,\n'
'        })\n'
'\n'
'    def to_schema(self):\n'
'        return {"type": "function", "function": {\n'
'            "name": self.name, "description": self.description,\n'
'            "parameters": {"type": "object",\n'
'                "properties": {\n'
'                    "limit": {"type": "integer", "description": "Number of emails (2-20). Use what the user said, or 5 for plural with no number."},\n'
'                    "filter": {"type": "string", "description": "Gmail search filter. Default: \'-in:spam -in:trash\'."},\n'
'                }, "required": ["limit"]},\n'
'        }}'
)

content2 = content[:s] + new_block + content[e:]

# ── 2. Add ReadMultipleEmailsTool to _TOOL_REGISTRY ────────────────────────
old_reg = '    "read_email":               ReadEmailTool,'
new_reg  = '    "read_email":               ReadEmailTool,\n    "read_multiple_emails":     ReadMultipleEmailsTool,'
if old_reg in content2 and '"read_multiple_emails"' not in content2:
    content2 = content2.replace(old_reg, new_reg, 1)
    print("Registry updated")
elif '"read_multiple_emails"' in content2:
    print("Registry already has read_multiple_emails")
else:
    print("WARNING: registry pattern not found")

# ── 3. Syntax check ────────────────────────────────────────────────────────
tmp = tempfile.mktemp(suffix='.py')
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(content2)
try:
    py_compile.compile(tmp, doraise=True)
    print("Syntax: OK")
except py_compile.PyCompileError as ex:
    print(f"Syntax ERROR: {ex}"); sys.exit(1)
finally:
    os.unlink(tmp)

# ── 4. Save with CRLF ──────────────────────────────────────────────────────
with open(OUT, 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(content2)
print(f"Saved. Lines: {len(content2.splitlines())}")
