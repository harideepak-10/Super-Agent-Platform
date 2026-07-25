"""Run from Windows cmd: python fix_attachment_auto.py"""
import subprocess, py_compile, tempfile, os

repo = r'C:\Users\HP\PycharmProjects\PythonProject1\Super Agent Platform'
rel  = 'superagent-django/apps/agents/views.py'
path = os.path.join(repo, rel.replace('/', os.sep))

# Pull complete correct file from git HEAD
result = subprocess.run(
    ['git', 'show', f'HEAD:{rel}'],
    cwd=repo, capture_output=True
)
if result.returncode != 0:
    print("ERROR: git show failed:", result.stderr.decode())
    exit(1)

content = result.stdout.decode('utf-8')

# 1. Bump version 23 → 24
c2 = content.replace('"version":     23,', '"version":     24,', 1)
if c2 != content:
    print("Version bumped to 24")
    content = c2
else:
    print("WARNING: version 23 not found")

# 2. Replace the entire READING & SUMMARISING section with clearer rules
old_section = (
    "\"=== READING & SUMMARISING EMAILS ===\\n\\n\"\n"
    "            \"Trigger this flow for ANY of these: 'read', 'check', 'summarize', 'show', 'what are my emails', 'any new emails'.\\n\\n\"\n"
    "            \"  Determine limit from the request:\\n\"\n"
    "            \"    'last email' / 'latest email' (singular, no number) → limit=1\\n\"\n"
    "            \"    'last N emails' / 'last N' → limit=N\\n\"\n"
    "            \"    'check my emails' / no number specified → limit=1\\n\\n\"\n"
    "            \"  1. Call read_email(limit=<limit>, filter='-in:spam -in:trash')\\n\"\n"
    "            \"  2. For EACH email returned (even if subject or body is empty):\\n\"\n"
    "            \"     - If full_body has content → summarize it\\n\"\n"
    "            \"     - If has_attachments is true → ALWAYS call read_email_attachment_content\\n\"\n"
    "            \"       with filter from:<sender_email> and summarize every attachment\\n\"\n"
    "            \"     - Do BOTH if body AND attachments present\\n\"\n"
    "            \"     - NEVER skip an email just because subject or body is empty — check has_attachments\\n\"\n"
    "            \"  3. Write the summary DIRECTLY in your response — do NOT call summarize_emails\\n\"\n"
    "            \"  STOP. Do NOT call send_email after summarizing.\\n\\n\""
)

new_section = (
    "\"=== READING & SUMMARISING EMAILS ===\\n\\n\"\n"
    "            \"Trigger this flow for ANY of these: 'read', 'check', 'summarize', 'show', 'what are my emails', 'any new emails'.\\n\\n\"\n"
    "            \"LIMIT RULE — default is ALWAYS 1. Only fetch more if the user says a number explicitly.\\n\"\n"
    "            \"  'check my email' / 'read my email' / 'last email' / 'my email' → limit=1\\n\"\n"
    "            \"  'last 2 emails' / 'recent 3' / 'last N' → limit=N\\n\"\n"
    "            \"  'check my emails' / 'read my emails' (plural, no number) → limit=5\\n\\n\"\n"
    "            \"  1. Call read_email(limit=<limit>, filter='-in:spam -in:trash')\\n\"\n"
    "            \"  2. For EACH email in the result (NEVER skip any email):\\n\"\n"
    "            \"     IF limit=1 (single email requested):\\n\"\n"
    "            \"       - Summarize body if it has content\\n\"\n"
    "            \"       - If has_attachments is true → ALWAYS call read_email_attachment_content and summarize\\n\"\n"
    "            \"       - Do BOTH if body AND attachments\\n\"\n"
    "            \"     IF limit>1 (multiple emails):\\n\"\n"
    "            \"       - Summarize body if it has content\\n\"\n"
    "            \"       - If body is empty AND has_attachments is true → write: '[Has attachment — ask me to read it for details]'\\n\"\n"
    "            \"       - Do NOT auto-read attachments in bulk — too many tokens\\n\"\n"
    "            \"  3. Write the summary DIRECTLY in your response — do NOT call summarize_emails\\n\"\n"
    "            \"  STOP. Do NOT call send_email after summarizing.\\n\\n\""
)

if old_section in content:
    content = content.replace(old_section, new_section)
    print("Reading section updated with v24 rules")
else:
    print("WARNING: reading section not found — checking partial match")
    if "LIMIT RULE" in content:
        print("Section already has v24 rules")
    else:
        print("ERROR: could not find section to replace")

# Write with CRLF so Windows git sees a real diff
with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(content)

lines = content.splitlines()
print(f"Saved. Lines: {len(lines)}, last: {repr(lines[-1])}")

# Syntax check
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
