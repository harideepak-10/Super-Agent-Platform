"""Run from Windows cmd: python fix_v24.py"""
import subprocess, py_compile, tempfile, os

repo = r'C:\Users\HP\PycharmProjects\PythonProject1\Super Agent Platform'
rel  = 'superagent-django/apps/agents/views.py'
path = os.path.join(repo, rel.replace('/', os.sep))

result = subprocess.run(['git', 'show', f'HEAD:{rel}'], cwd=repo, capture_output=True)
if result.returncode != 0:
    print("ERROR:", result.stderr.decode()); exit(1)

content = result.stdout.decode('utf-8')
BS, NL = chr(92), chr(10)

# ── 1. Bump version 26 → 27 ────────────────────────────────────────────────
c2 = content.replace('"version":     26,', '"version":     27,', 1)
if c2 != content:
    print("Version bumped 26 → 27"); content = c2
else:
    print("WARNING: version 26 not found")

# ── 2. Add read_multiple_emails to tools list ───────────────────────────────
old_tools = '            # Read\n            "read_email", "search_emails"'
new_tools  = '            # Read\n            "read_email", "read_multiple_emails", "search_emails"'

if old_tools in content:
    content = content.replace(old_tools, new_tools, 1)
    print("read_multiple_emails added to tools list")
elif '"read_multiple_emails"' in content:
    print("read_multiple_emails already in tools list")
else:
    print("WARNING: tools list pattern not found")

# ── 3. Update system prompt LIMIT section → two-tool routing ───────────────
old_limit = (
    'LIMIT — always 1 unless user gives a number:' + BS + 'n"\n'
    '            "  \'last\' or singular \'email\' with no number → limit=1' + BS + 'n"\n'
    '            "  \'last N\' / \'recent N\' / \'N emails\' → limit=N' + BS + 'n"\n'
    '            "  plural \'emails\' with no number → limit=5' + BS + 'n' + BS + 'n"\n'
    '            "  1. Call read_email(limit=<limit>, filter=\'-in:spam -in:trash\')'
)

new_limit = (
    'TOOL SELECTION:' + BS + 'n"\n'
    '            "  Single email requests: \'read my email\' / \'last email\' / \'check my email\'' + BS + 'n"\n'
    '            "    → call read_email (no limit parameter)' + BS + 'n"\n'
    '            "  Multiple emails with explicit number: \'last 5 emails\' / \'recent 3\'' + BS + 'n"\n'
    '            "    → call read_multiple_emails(limit=N)' + BS + 'n"\n'
    '            "  Plural emails with no number: \'check my emails\' / \'read my emails\'' + BS + 'n"\n'
    '            "    → call read_multiple_emails(limit=5)' + BS + 'n' + BS + 'n"\n'
    '            "  1. Call the correct tool: read_email OR read_multiple_emails(limit=N)'
)

if old_limit in content:
    content = content.replace(old_limit, new_limit, 1)
    print("System prompt updated to two-tool routing")
elif 'TOOL SELECTION' in content:
    print("System prompt already updated")
else:
    print("WARNING: LIMIT section pattern not found")

# ── 4. Save with CRLF ─────────────────────────────────────────────────────
with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(content)

lines = content.splitlines()
print(f"Saved. Lines: {len(lines)}, last: {repr(lines[-1])}")

# ── 5. Syntax check ───────────────────────────────────────────────────────
tmp = tempfile.mktemp(suffix='.py')
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(content)
try:
    py_compile.compile(tmp, doraise=True)
    print("Syntax: OK")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
finally:
    os.unlink(tmp)
