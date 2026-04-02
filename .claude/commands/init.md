You are re-initialising the Cairn Protocol. Execute these steps now in order
before resuming any work.

Step 1 — confirm Cairn API is running:
Call get_project_status() or GET http://localhost:8765/health
If offline, output the start command and stop.

Step 2 — re-read your instructions:
Read D:\claw\CLAUDE.md
Read D:\claw\CAIRN_PROTOCOL.md
Read D:\claw\projects\<current_project>\core.md

Step 3 — pull fresh memory:
retrieve_codebase_context(query=<last task>, project=<current_project>, limit=10)
retrieve_chat_history(query=<last task>, project=<current_project>, limit=10)

Step 4 — output this exact status block:

CAIRN INITIALISED
─────────────────────────────────────
Project:        <project_name>
API:            online (port 8765)
Memory entries: <count>
Last write:     <timestamp>
Models ready:   <available tiers>
─────────────────────────────────────
Ready. What are we building?
