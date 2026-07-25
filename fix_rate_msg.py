"""Run from Windows cmd: python fix_rate_msg.py"""
import os, py_compile, tempfile

files = [
    r'C:\Users\HP\PycharmProjects\PythonProject1\Super Agent Platform\superagent-django\core\llm\groq_provider.py',
    r'C:\Users\HP\PycharmProjects\PythonProject1\Super Agent Platform\superagent-ai\core\llm\groq_provider.py',
]

old = (
    '"⚠️ The AI is temporarily busy due to high usage (Groq rate limit reached). "\n'
    '    "Please wait about 1 minute and try again."'
)
new = (
    '"⚠️ AI usage limit reached. Please try again later."'
)

for path in files:
    with open(path, 'rb') as f:
        raw = f.read()
    content = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n').decode('utf-8')

    if old in content:
        content = content.replace(old, new)
        print(f"Fixed: {os.path.basename(path)}")
    else:
        print(f"Already fixed or pattern not found: {os.path.basename(path)}")

    with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(content)

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
