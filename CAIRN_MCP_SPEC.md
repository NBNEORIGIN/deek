# CAIRN_MCP_SPEC.md
# Cairn MCP Server Specification
# North By North East Print & Sign Ltd
# Last updated: 29 March 2026

---

## Overview

Cairn exposes its memory and retrieval capabilities as an MCP (Model Context Protocol)
server. This allows Claude Code, Codex, and any other MCP-compatible head model to
treat Cairn's memory as native tools — indistinguishable from file reads or git
operations.

The MCP server wraps Cairn's existing FastAPI endpoints at http://localhost:8765.
No new backend logic is required — this is purely an interface layer.

---

## MCP Server Location

```
D:\claw\mcp\cairn_mcp_server.py
```

Run alongside the Cairn API:
```powershell
cd D:\claw
python mcp\cairn_mcp_server.py
```

Or add to `build-cairn.bat` so it starts automatically with the API.

---

## Tool Definitions

### 1. retrieve_codebase_context

Retrieves semantically and lexically relevant code chunks from the indexed codebase.
Uses hybrid BM25 + pgvector retrieval with RRF merge.

```json
{
  "name": "retrieve_codebase_context",
  "description": "Retrieve relevant code chunks from the Cairn codebase index using hybrid BM25 + semantic search. Use before starting any development task to surface prior decisions, related code, and architectural context.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language description of what you are looking for. Be specific — include function names, file names, or concepts."
      },
      "project": {
        "type": "string",
        "description": "Project name to scope the search. One of: claw, phloe, render, crm, bookkeeping, studio, houseofhair, clayport, a1g"
      },
      "limit": {
        "type": "integer",
        "description": "Maximum number of chunks to return. Default 10, max 25.",
        "default": 10
      },
      "hybrid": {
        "type": "boolean",
        "description": "Use hybrid BM25 + pgvector retrieval. Default true. Set false for pure semantic search.",
        "default": true
      }
    },
    "required": ["query", "project"]
  }
}
```

**Backend call**: `GET http://localhost:8765/retrieve?query={query}&project={project}&limit={limit}`

**Returns**:
```json
{
  "chunks": [
    {
      "id": "chunk_uuid",
      "file_path": "core/tools/git_tools.py",
      "content": "...",
      "score": 0.87,
      "retrieval_method": "hybrid_rrf"
    }
  ],
  "total": 10,
  "project": "claw",
  "query": "git commit tool mapping"
}
```

---

### 2. retrieve_chat_history

Retrieves relevant development chat history and prior session decisions from memory.
Use to surface what was tried, what failed, and what was decided in previous sessions.

```json
{
  "name": "retrieve_chat_history",
  "description": "Retrieve relevant development decisions and chat history from Cairn memory. Use to avoid repeating past mistakes, understand prior approaches, and maintain continuity across sessions.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language description of the decision or topic you are looking for."
      },
      "project": {
        "type": "string",
        "description": "Project name to scope the search."
      },
      "limit": {
        "type": "integer",
        "description": "Maximum number of memory entries to return. Default 10.",
        "default": 10
      },
      "outcome_filter": {
        "type": "string",
        "description": "Filter by outcome. One of: committed, partial, failed, deferred. Omit for all.",
        "enum": ["committed", "partial", "failed", "deferred"]
      }
    },
    "required": ["query", "project"]
  }
}
```

**Backend call**: `GET http://localhost:8765/memory/retrieve?query={query}&project={project}&limit={limit}`

**Returns**:
```json
{
  "entries": [
    {
      "id": "entry_uuid",
      "query": "fix git_commit tool mapping",
      "decision": "Found tool name mismatch in registry.py line 47...",
      "rejected": "Considered renaming the function in git_tools.py instead",
      "outcome": "committed",
      "model": "claude-sonnet-4-6",
      "files_changed": ["core/tools/registry.py"],
      "created_at": "2026-03-29T21:45:00Z"
    }
  ],
  "total": 1,
  "project": "claw"
}
```

---

### 3. update_memory

Writes a memory entry to Cairn after completing a task. This is the write-back
tool. Call this after every non-trivial action.

```json
{
  "name": "update_memory",
  "description": "Write a memory entry to Cairn after completing a task. Required after every development action that involved a decision, fix, or discovery. This is how Cairn learns.",
  "input_schema": {
    "type": "object",
    "properties": {
      "project": {
        "type": "string",
        "description": "Project this entry belongs to."
      },
      "query": {
        "type": "string",
        "description": "The original task or question that was addressed."
      },
      "decision": {
        "type": "string",
        "description": "What was done and why. Be specific — include file names, line numbers, and reasoning."
      },
      "rejected": {
        "type": "string",
        "description": "What was considered and ruled out. Critical for preventing repeated dead ends."
      },
      "outcome": {
        "type": "string",
        "description": "Result of the task.",
        "enum": ["committed", "partial", "failed", "deferred"]
      },
      "model": {
        "type": "string",
        "description": "Model that performed the primary work (e.g. claude-sonnet-4-6, deepseek-chat, qwen2.5-coder:32b)."
      },
      "files_changed": {
        "type": "array",
        "items": {"type": "string"},
        "description": "List of files modified. Empty array if none."
      },
      "write_model": {
        "type": "string",
        "description": "Model to use for summarising this entry. Omit to use default routing.",
        "enum": ["qwen", "deepseek", "sonnet", "opus"]
      }
    },
    "required": ["project", "query", "decision", "outcome", "model", "files_changed"]
  }
}
```

**Backend call**: `POST http://localhost:8765/memory/write`

**Returns**:
```json
{
  "id": "entry_uuid",
  "project": "claw",
  "outcome": "committed",
  "written_at": "2026-03-29T21:45:00Z"
}
```

---

### 4. list_projects

Returns all projects currently loaded in Cairn with their indexing status.

```json
{
  "name": "list_projects",
  "description": "List all projects currently loaded in Cairn, with file counts, chunk counts, and index status. Use at session start to confirm which projects are available.",
  "input_schema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

**Backend call**: `GET http://localhost:8765/projects`

**Returns**:
```json
{
  "projects": [
    {
      "name": "claw",
      "path": "D:\\claw",
      "files": 59,
      "chunks": 477,
      "last_indexed": "2026-03-29T20:00:00Z",
      "status": "ready"
    },
    {
      "name": "phloe",
      "path": "D:\\nbne_business\\nbne_platform",
      "files": 260,
      "chunks": 2053,
      "last_indexed": "2026-03-29T20:00:00Z",
      "status": "ready"
    }
  ],
  "total": 4
}
```

---

### 5. get_project_status

Returns the health and status of Cairn itself — API, file watcher, model
availability, and memory entry counts.

```json
{
  "name": "get_project_status",
  "description": "Get the current health and status of Cairn — API, file watcher, model tiers, and memory. Use at session start to confirm the system is ready before beginning work.",
  "input_schema": {
    "type": "object",
    "properties": {
      "project": {
        "type": "string",
        "description": "Optional. If provided, returns project-specific stats including memory entry count."
      }
    },
    "required": []
  }
}
```

**Backend call**: `GET http://localhost:8765/health`

**Returns**:
```json
{
  "status": "healthy",
  "api": "online",
  "file_watcher": "running",
  "models": {
    "tier1_local": {"model": "qwen2.5-coder:7b", "available": true},
    "tier2_deepseek": {"model": "deepseek-chat", "available": true},
    "tier3_sonnet": {"model": "claude-sonnet-4-6", "available": true},
    "tier4_opus": {"model": "claude-opus-4-6", "available": true},
    "fallback_openai": {"model": "gpt-4o", "available": true}
  },
  "memory": {
    "total_entries": 142,
    "project_entries": 38,
    "last_write": "2026-03-29T21:45:00Z"
  },
  "force_api": true
}
```

---


---

### 6. get_business_context

Assembles a snapshot of NBNE business state by querying all available module
context endpoints. This is the primary tool for the business brain.

```json
{
  "name": "get_business_context",
  "description": "Assemble current NBNE business state from all module context endpoints (Manufacture, Ledger, Marketing). Use when reasoning across the business rather than within a single codebase. Returns make list, financial position, and marketing pipeline in one call.",
  "input_schema": {
    "type": "object",
    "properties": {
      "modules": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Modules to query. Omit for all available. Options: manufacture, ledger, marketing",
        "default": ["manufacture", "ledger", "marketing"]
      },
      "include_recommendations": {
        "type": "boolean",
        "description": "Whether to include brain recommendations in the response.",
        "default": true
      }
    },
    "required": []
  }
}
```

**Backend call**: Cairn aggregates:
- `GET http://localhost:8002/api/cairn/context` (Manufacture)
- `GET http://localhost:8001/api/cairn/context` (Ledger)
- `GET http://localhost:8004/api/cairn/context` (Marketing/CRM)

Full schema: `CAIRN_MODULES.md`


---

### 7. log_cost

Logs the cost of every model used in a prompt. Called after every prompt
alongside update_memory. Writes to both PostgreSQL and CSV.

```json
{
  "name": "log_cost",
  "description": "Log the cost of every model used in this prompt. Required after every prompt. Enables cost-benefit analysis of local vs API models and hardware ROI tracking.",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {
        "type": "string",
        "description": "Current session identifier."
      },
      "prompt_summary": {
        "type": "string",
        "description": "One line description of the task this prompt addressed."
      },
      "project": {
        "type": "string",
        "description": "Project this prompt was working on."
      },
      "costs": {
        "type": "array",
        "description": "Cost entry per model used.",
        "items": {
          "type": "object",
          "properties": {
            "model": {"type": "string"},
            "tokens_in": {"type": "integer"},
            "tokens_out": {"type": "integer"},
            "cost_gbp": {"type": "number"}
          },
          "required": ["model", "tokens_in", "tokens_out", "cost_gbp"]
        }
      },
      "total_cost_gbp": {
        "type": "number",
        "description": "Sum of all model costs for this prompt."
      }
    },
    "required": ["session_id", "prompt_summary", "project", "costs", "total_cost_gbp"]
  }
}
```

**Backend call**: `POST http://localhost:8765/costs/log`

**Writes to**:
- PostgreSQL `cost_log` table
- `data/cost_log.csv` (append only)

**PostgreSQL schema**:
```sql
CREATE TABLE cost_log (
  id          SERIAL PRIMARY KEY,
  session_id  TEXT NOT NULL,
  prompt_summary TEXT,
  project     TEXT,
  model       TEXT NOT NULL,
  tokens_in   INTEGER DEFAULT 0,
  tokens_out  INTEGER DEFAULT 0,
  cost_gbp    NUMERIC(10,6) DEFAULT 0,
  logged_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**CSV format** (`data/cost_log.csv`):
```
timestamp,session_id,project,prompt_summary,model,tokens_in,tokens_out,cost_gbp,total_cost_gbp
2026-03-30T08:00:00Z,sess_001,claw,fix git_commit tool,qwen2.5-coder:32b,0,0,0.000000,0.003240
2026-03-30T08:00:00Z,sess_001,claw,fix git_commit tool,deepseek-chat,1200,480,0.003240,0.003240
```

**Returns**:
```json
{
  "logged": true,
  "session_id": "sess_001",
  "total_cost_gbp": 0.003240,
  "running_session_total_gbp": 0.018400
}
```

## MCP Server Implementation

Build `D:\claw\mcp\cairn_mcp_server.py` as a thin wrapper:

```python
"""
Cairn MCP Server
Wraps Cairn FastAPI endpoints as MCP tools for Claude Code and compatible clients.
"""

import asyncio
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

CAIRN_BASE_URL = "http://localhost:8765"
server = Server("cairn")

@server.list_tools()
async def list_tools():
    # Return all 5 tool definitions as defined in CAIRN_MCP_SPEC.md
    ...

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    async with httpx.AsyncClient(timeout=30.0) as client:
        if name == "retrieve_codebase_context":
            r = await client.get(f"{CAIRN_BASE_URL}/retrieve", params=arguments)
            return [types.TextContent(type="text", text=r.text)]

        elif name == "retrieve_chat_history":
            r = await client.get(f"{CAIRN_BASE_URL}/memory/retrieve", params=arguments)
            return [types.TextContent(type="text", text=r.text)]

        elif name == "update_memory":
            r = await client.post(f"{CAIRN_BASE_URL}/memory/write", json=arguments)
            return [types.TextContent(type="text", text=r.text)]

        elif name == "list_projects":
            r = await client.get(f"{CAIRN_BASE_URL}/projects")
            return [types.TextContent(type="text", text=r.text)]

        elif name == "get_project_status":
            params = {"project": arguments["project"]} if "project" in arguments else {}
            r = await client.get(f"{CAIRN_BASE_URL}/health", params=params)
            return [types.TextContent(type="text", text=r.text)]

async def main():
    async with stdio_server() as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

Install MCP SDK: `pip install mcp --break-system-packages`

Register in Claude Code's MCP config (`~/.claude/mcp_settings.json` or equivalent):
```json
{
  "mcpServers": {
    "cairn": {
      "command": "python",
      "args": ["D:\\claw\\mcp\\cairn_mcp_server.py"],
      "env": {}
    }
  }
}
```

---

## Structured Output Requirements

All junior models (Qwen, DeepSeek) must return outputs in the following formats.
CC enforces this in delegation prompts — do not accept free-form prose from junior
models for development tasks.

### Plan format (before implementation)
```json
{
  "task": "short description",
  "approach": "what will be done",
  "files_to_modify": ["file1.py", "file2.py"],
  "risks": ["risk1", "risk2"],
  "confidence": "high|medium|low"
}
```

### Diff format (implementation output)
```
--- a/path/to/file.py
+++ b/path/to/file.py
@@ -line,count +line,count @@
 context line
-removed line
+added line
 context line
```

Standard unified diff only. No prose around it. CC applies the diff and reviews.

### Review format (Sonnet/Opus sign-off)
```json
{
  "verdict": "approve|reject|request_changes",
  "summary": "one sentence",
  "issues": ["issue1 if any"],
  "approved_for_commit": true
}
```

Nothing gets committed without `approved_for_commit: true` from the reviewer tier.

---

## Implementation Priority

This MCP server should be built by CC in the next Cairn development session,
after the git_commit tool fix. It is the most important infrastructure improvement
available — it makes Cairn's memory native to any MCP-compatible head model rather
than requiring HTTP calls in the protocol.

Estimated complexity: low. The backend already exists. This is a thin wrapper.
Assign to: DeepSeek for implementation, Sonnet for review.
