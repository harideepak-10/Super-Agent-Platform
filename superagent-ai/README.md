# Super Agent AI Platform

A Python-based AI agent system where multiple specialised agents work together to automate small business tasks — reading emails, organising invoices, preparing reports. Nothing is sent externally without human approval.

---

## Project Structure

```
superagent-ai/
├── core/
│   ├── base_agent.py          # Core ReAct loop + custom exceptions
│   ├── llm/
│   │   ├── base.py            # LLMProvider abstract base class
│   │   ├── groq_provider.py   # Groq API (llama-3.1-8b-instant)
│   │   └── mock_provider.py   # Deterministic mock for testing
│   ├── tools/
│   │   ├── base_tool.py       # BaseTool + ToolZone (GREEN/YELLOW/RED)
│   │   ├── calculator.py      # Safe arithmetic evaluator
│   │   ├── current_time.py    # Current date/time info
│   │   └── echo.py            # Test echo tool
│   └── memory/
│       └── working_memory.py  # Per-task key-value store
├── agents/
│   └── email_agent.py         # Email management agent
├── tests/
│   ├── test_base_agent.py     # 21 tests for BaseAgent
│   ├── test_tools.py          # 34 tests for all tools
│   └── test_email_agent.py    # 20 tests for EmailAgent
└── requirements.txt
```

---

## Quick Start

### 1. Clone and enter the project

```bash
cd superagent-ai
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your Groq API key

Create a `.env` file in `superagent-ai/`:

```
GROQ_API_KEY=your_groq_api_key_here
```

Or export it in your shell:

```bash
export GROQ_API_KEY=your_groq_api_key_here
```

Get a free API key at [console.groq.com](https://console.groq.com).

### 4. Run the tests

```bash
PYTHONPATH=. pytest tests/ -v
```

Expected output: **75 passed**.

---

## Tool Safety Zones

Every tool declares a zone that controls whether the agent can run it automatically:

| Zone   | Behaviour |
|--------|-----------|
| GREEN  | Runs immediately — no approval needed |
| YELLOW | Agent pauses and raises `ApprovalRequired` — human must approve |
| RED    | Agent raises `RedZoneBlocked` — human executes manually |

The zone check is enforced in `BaseAgent` and **cannot be bypassed**.

---

## Using EmailAgent

```python
from agents.email_agent import EmailAgent
from core.llm.groq_provider import GroqProvider

agent = EmailAgent(llm_provider=GroqProvider())

try:
    result = agent.run("Classify this email: 'Invoice #1042 attached.'")
    print(result)
except Exception as e:
    print(f"Error: {e}")

# Inspect what happened
for entry in agent.get_audit_log():
    print(entry["event_type"], entry["step_number"])
```

---

## Using MockLLMProvider (in tests)

```python
from core.llm.mock_provider import MockLLMProvider
from agents.email_agent import EmailAgent

mock = MockLLMProvider([
    {"content": "This is an invoice email.", "tool_call": None}
])
agent = EmailAgent(llm_provider=mock)
result = agent.run("Classify this email.")
print(result)  # "This is an invoice email."
```

---

## Handling the Approval Gate

When the agent tries to use a YELLOW zone tool, it raises `ApprovalRequired`:

```python
from core.base_agent import ApprovalRequired, RedZoneBlocked

try:
    result = agent.run("Send a reply to the customer.")
except ApprovalRequired as e:
    print(f"Approval needed for tool: {e.tool_name}")
    print(f"Proposed input: {e.tool_input}")
    # Human reviews and either approves or rejects
except RedZoneBlocked as e:
    print(f"Blocked: {e.tool_name} — must be executed by a human.")
```

---

## Audit Log

Every action is recorded and accessible after a run:

```python
agent.run("Some task.")
log = agent.get_audit_log()
# Each entry: timestamp, event_type, details, step_number, cost_so_far

summary = agent.get_cost_summary()
print(summary)  # {"total_cost_usd": 0.000123, "total_steps": 3}
```

Event types recorded: `task_started`, `llm_called`, `tool_called`, `tool_result`, `approval_needed`, `task_completed`, `error`, `cost_limit_reached`, `step_limit_reached`.

---

## Environment Variables

| Variable      | Required | Description |
|---------------|----------|-------------|
| `GROQ_API_KEY` | Yes (production) | Your Groq API key. Not needed for tests. |

---

## Next Steps

- **Gmail integration** — connect Gmail API tools (YELLOW zone for send, GREEN for read)
- **Google Drive integration** — read and organise invoices from Drive
- **InvoiceAgent** — extract line items and totals from invoice PDFs
- **ReportAgent** — compile weekly summaries from email and invoice data
- **Orchestrator** — coordinate multiple agents on a single business task
