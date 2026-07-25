"""Run from Windows cmd: python fix_spam_filter.py"""
import os, py_compile, tempfile, re

path = r'C:\Users\HP\PycharmProjects\PythonProject1\Super Agent Platform\superagent-django\apps\agents\views.py'

with open(path, 'rb') as f:
    raw = f.read()
content = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n').decode('utf-8')

old = (
    '"=== READ EMAIL RULES ===\\n\\n"\n'
    '            "ALWAYS use filter \'-in:spam -in:trash\' by default (ALL emails, read + unread).\\n"\n'
    '            "ONLY use \'is:unread\' if the user explicitly says \'unread\' or \'new emails\'.\\n\\n"\n'
    '            "  \'read my last 5 emails\'  → filter: \'-in:spam -in:trash\', limit: 5\\n"\n'
    '            "  \'recent emails\'          → filter: \'-in:spam -in:trash\', limit: 10\\n"\n'
    '            "  \'unread emails\'          → filter: \'is:unread -in:spam -in:trash\', limit: 10\\n"\n'
    '            "  NEVER fetch more than 10 unread emails at once — too many tokens.\\n"\n'
    '            "  If user says \'all unread\' or implies a large number, still cap at 10 and tell them.\\n\\n"'
)

new = (
    '"=== READ EMAIL RULES ===\\n\\n"\n'
    '            "DEFAULT filter (when user does NOT mention spam or trash): \'-in:spam -in:trash\'\\n"\n'
    '            "ONLY use \'is:unread\' if the user explicitly says \'unread\' or \'new emails\'.\\n\\n"\n'
    '            "  \'read my last 5 emails\'       → filter: \'-in:spam -in:trash\', limit: 5\\n"\n'
    '            "  \'recent emails\'               → filter: \'-in:spam -in:trash\', limit: 10\\n"\n'
    '            "  \'unread emails\'               → filter: \'is:unread -in:spam -in:trash\', limit: 10\\n"\n'
    '            "  \'emails from spam\'            → filter: \'in:spam\', limit: 10\\n"\n'
    '            "  \'unread emails from spam\'     → filter: \'in:spam is:unread\', limit: 10\\n"\n'
    '            "  \'check my spam\'               → filter: \'in:spam\', limit: 10\\n"\n'
    '            "  NEVER fetch more than 10 unread emails at once — too many tokens.\\n"\n'
    '            "  If user says \'all unread\' or implies a large number, still cap at 10 and tell them.\\n\\n"'
)

# Also bump version 18 -> 19
content2 = content.replace('"version":     18,', '"version":     19,')
if content2 != content:
    print("Version bumped to 19")
    content = content2

if old in content:
    content = content.replace(old, new)
    print("Fixed: spam filter rules added")
else:
    print("Pattern not found — checking version bump only")
    if '"version":     19' in content:
        print("Version already 19, proceeding")

with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(content)

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
