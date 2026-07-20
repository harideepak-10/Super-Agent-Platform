"""Run from Windows cmd: python fix_email_tokens.py"""
import os, py_compile, tempfile

files = [
    r'C:\Users\HP\PycharmProjects\PythonProject1\Super Agent Platform\superagent-django\core\tools\gmail\read_emails.py',
    r'C:\Users\HP\PycharmProjects\PythonProject1\Super Agent Platform\superagent-ai\core\tools\gmail\read_emails.py',
]

for path in files:
    with open(path, 'rb') as f:
        raw = f.read()
    content = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n').decode('utf-8')

    old = '_FULL_BODY_MAX_CHARS  = 2000                    # enough for summaries, safe on tokens'
    new = '_FULL_BODY_MAX_CHARS  = 600                     # keep 10-email calls under 6000 TPM Groq limit'

    if old in content:
        content = content.replace(old, new)
        print(f"Fixed: {os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))}/{os.path.basename(path)}")
    elif '600' in content:
        print(f"Already fixed: {os.path.basename(path)}")
    else:
        print(f"Pattern not found in: {path}")
        continue

    # Write back with CRLF so Windows git detects the change
    with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(content)

    # Verify syntax
    tmp = tempfile.mktemp(suffix='.py')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    try:
        py_compile.compile(tmp, doraise=True)
        print(f"  Syntax: OK")
    except py_compile.PyCompileError as e:
        print(f"  Syntax ERROR: {e}")
    finally:
        os.unlink(tmp)

print("\nDone. Now run in Git Bash:")
print('  git add superagent-django/core/tools/gmail/read_emails.py superagent-ai/core/tools/gmail/read_emails.py')
print('  git commit -m "fix: reduce email body to 600 chars to stay under Groq 6000 TPM"')
print('  git push')
