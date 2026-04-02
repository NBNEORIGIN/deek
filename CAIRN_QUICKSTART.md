# Cairn Quickstart Guide
# North By North East Print & Sign Ltd
# For the NBNE team — no coding knowledge required

---

## Starting Cairn

Run `build-cairn.bat` from D:\claw. This starts:
- The Cairn API on port 8765
- The MCP server (if built)

Wait for the terminal windows to show they are running before sending prompts.

If Cairn is already running, you do not need to start it again.

---

## Sending a Prompt

Open any CMD or PowerShell window and type:

```
cairn "your task here" project_name
```

Examples:
```
cairn "fix the login bug on the booking page" phloe
cairn "add a new blank type called WINDSOR" manufacture
cairn "list all projects and their status" claw
```

Cairn will:
1. Check its memory for related prior work
2. Route the task to the right model (local, DeepSeek, Sonnet, or Opus)
3. Do the work
4. Write back what it learned to memory
5. Log the cost

---

## Project Names

Use these exact names when specifying a project:

| Name | What it is |
|---|---|
| claw | Cairn itself (the AI development system) |
| phloe | WaaS booking platform (DemNurse, Ganbaru Kai, etc.) |
| render | Product design and publishing (Signmaker) |
| manufacture | Make list, stock, production tracking |
| ledger | Bookkeeping and financial tracking |
| crm | Customer relationship management |

If you forget the project name, just run `cairn "anything"` without a project
and it will list what is available.

---

## Flags

### -NoMemory
Skip the memory lookup step. Use when you know the task is completely new
and unrelated to anything done before.

```
cairn "scaffold a brand new module" claw -NoMemory
```

### -Opus
Force the task to use Claude Opus (the most capable and expensive model).
Use for architecture decisions, security-sensitive changes, or complex
cross-project reasoning.

```
cairn "redesign the tenant isolation layer" phloe -Opus
```

### /init (inside Claude Code)
If you are already in a Claude Code session and it seems to have lost context,
type `/init` to re-anchor the Cairn protocol. This re-reads all instructions,
pulls fresh memory, and confirms the system is ready.

---

## Checking Costs

Cairn logs the cost of every prompt.

**Quick check**: Open `D:\claw\data\cost_log.csv` in Excel or any text editor.
Each row shows the timestamp, project, model used, tokens, and cost in GBP.

**Dashboard**: Visit http://localhost:3000/status when the web UI is running
for a visual cost breakdown.

**Key insight**: Local model prompts (Qwen) cost nothing. The cost log helps
track how much the RTX 3090 saves vs sending everything to the API.

---

## If Cairn Is Offline

If you get a "CAIRN OFFLINE" message:

1. Open a terminal
2. Navigate to D:\claw
3. Run: `build-cairn.bat`
4. Wait for the API to show "Uvicorn running on http://0.0.0.0:8765"
5. Try your prompt again

If the API starts but immediately crashes, check:
- Is PostgreSQL running? (Cairn needs it for memory and embeddings)
- Is the .env file present at D:\claw\.env?
- Is the virtual environment intact? Run: `.\.venv\Scripts\python --version`

Report persistent issues to Toby.

---

## The Principle

Cairn gets smarter every time it is used. Every prompt that writes back to
memory makes the next prompt faster and more accurate. Every decision captured
is a mistake that will never be repeated.

The memory is the product. The code stays in Northumberland.
