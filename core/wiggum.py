"""
WIGGUM — Workbench Iterative Goal-Guided Update Machine

The outer loop that drives CLAW toward an end goal.

Architecture:
    OUTER LOOP (WiggumOrchestrator) — this file
        Holds: goal, success criteria, iteration history
        Does:  assess state → plan task → delegate to CLAW → repeat

    INNER LOOP (ClawAgent) — core/agent.py
        Does:  implement the task, call tools, return result

    HUMAN
        Holds: approval gate for REVIEW/DESTRUCTIVE tools
        Reviews: final changes before commit/push

Phases per iteration:
    1. ASSESS  — read-only CLAW call: evaluate codebase vs. success criteria
    2. CHECK   — parse for PASS/PARTIAL/FAIL per criterion
    3. PLAN    — read-only CLAW call: determine highest-priority next task
    4. EXECUTE — full CLAW call: implement the task
    5. RECORD  — save iteration to history

Safety model:
    - REVIEW/DESTRUCTIVE tools always pause for human approval
    - The loop does NOT block on approvals; it records the pending approval
      and continues assessing/planning while the human reviews
    - Nothing is committed or pushed without explicit user confirmation
"""
import uuid
import asyncio
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import ClawAgent


class WiggumOrchestrator:

    MAX_ITERATIONS = 20

    def __init__(
        self,
        goal: str,
        success_criteria: list[str],
        project_id: str,
        api_url: str = '',
        api_key: str = '',
        session_id: str | None = None,
        max_iterations: int = MAX_ITERATIONS,
        agent: 'ClawAgent | None' = None,
        auto_approve_review: bool = True,
        batch_mode: bool = False,
    ):
        self.goal = goal
        self.success_criteria = success_criteria
        self.project_id = project_id
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.session_id = session_id or str(uuid.uuid4())
        self.max_iterations = max_iterations
        # When provided, calls go directly to the agent (no HTTP round-trip).
        # This is the preferred mode when the orchestrator runs in-process.
        self._agent = agent
        # Auto-execute REVIEW tools (file edits) during execute passes.
        # DESTRUCTIVE tools (git push, shell commands) are never auto-approved.
        self.auto_approve_review = auto_approve_review
        # When True, REVIEW tools are queued in pending_approvals rather than
        # blocking the loop. The loop continues; human approves the batch later.
        # DESTRUCTIVE tools always block regardless of this flag.
        # When False (default): behaviour is identical to the old implementation.
        self.batch_mode = batch_mode

        self.iteration = 0
        self.history: list[dict] = []
        self.status = 'pending'   # pending | running | complete | max_iterations | error
        self.pending_approvals: list[dict] = []

    # ─── Public ───────────────────────────────────────────────────────────────

    async def run(self) -> dict:
        """
        Main WIGGUM loop.
        Runs until all criteria are PASS or max_iterations is reached.
        Returns a result dict suitable for the /wiggum/{run_id} response.
        """
        self.status = 'running'
        started_at = datetime.utcnow()
        final_assessment = ''

        while self.iteration < self.max_iterations:
            self.iteration += 1
            print(f'[wiggum] Iteration {self.iteration}/{self.max_iterations}')

            # 1. Assess
            assessment = await self._assess_state()
            final_assessment = assessment

            # 2. Check success
            if self._evaluate_success(assessment):
                self.status = 'complete'
                print(f'[wiggum] ✓ All criteria met in {self.iteration} iterations')
                break

            # 3. Plan
            task = await self._plan_next_task(assessment)
            print(f'[wiggum] Next task: {task[:120]}')

            # If plan returned empty, criteria are met but evaluate_success
            # missed the format — treat as complete.
            if not task.strip():
                print(f'[wiggum] No unmet criteria found — treating as complete')
                self.status = 'complete'
                break

            # 4. Execute
            result = await self._call_claw(task, read_only=False)

            # Track any approval that was surfaced
            if result.get('pending_tool_call'):
                self.pending_approvals.append({
                    'iteration': self.iteration,
                    'task': task,
                    'pending_tool_call': result['pending_tool_call'],
                })

            # 5. Record
            self.history.append({
                'iteration': self.iteration,
                'assessment_summary': assessment[:300],
                'task': task[:300],
                'result_summary': result.get('content', '')[:300],
                'model_used': result.get('model_used', ''),
                'cost_usd': result.get('cost_usd', 0.0),
                'has_pending_approval': bool(result.get('pending_tool_call')),
            })

        else:
            self.status = 'max_iterations'
            # Final assessment without plan/execute
            final_assessment = await self._assess_state()

        elapsed = (datetime.utcnow() - started_at).total_seconds()
        result = {
            'status': self.status,
            'iterations': self.iteration,
            'elapsed_seconds': round(elapsed, 1),
            'final_assessment': final_assessment,
            'history': self.history,
            'pending_approvals': self.pending_approvals,
            'session_id': self.session_id,
            'total_cost_usd': round(
                sum(h.get('cost_usd', 0) for h in self.history), 4
            ),
        }

        # Post-run: append session summary to core.md (best-effort, never blocks)
        if self.status in ('complete', 'max_iterations') and self.history:
            try:
                from core.memory.summariser import SessionSummariser
                summariser = SessionSummariser(self.project_id)
                messages = [
                    {'role': 'user', 'content': h['task']}
                    for h in self.history
                ] + [
                    {'role': 'assistant', 'content': h['result_summary']}
                    for h in self.history
                ]
                await summariser.summarise(
                    messages,
                    wiggum_run_id=self.session_id[:8],
                )
            except Exception as exc:
                print(f'[wiggum] summariser failed (non-fatal): {exc}')

        return result

    # ─── Private ──────────────────────────────────────────────────────────────

    async def _assess_state(self) -> str:
        """
        Read-only CLAW call: evaluate the codebase against the success criteria.
        Returns the assessment text from the model.
        """
        criteria_lines = '\n'.join(
            f'  CRITERION_{i+1}: {c}' for i, c in enumerate(self.success_criteria)
        )
        prompt = (
            f'You are assessing code progress. Goal: {self.goal}\n\n'
            f'Read the relevant files using your tools, then output ONLY a '
            f'structured assessment block — no other text.\n\n'
            f'Format (use exactly these labels):\n'
            f'{criteria_lines}\n\n'
            f'For each criterion output exactly:\n'
            f'CRITERION_N: PASS | <one-line reason>\n'
            f'CRITERION_N: PARTIAL | <what exists> | missing: <what is needed>\n'
            f'CRITERION_N: FAIL | <what is missing or broken>\n\n'
            f'Rules:\n'
            f'- Do NOT make any changes to files\n'
            f'- Do NOT add commentary outside the structured block\n'
            f'- Check actual files with read_file or search_code before deciding\n'
            f'- Be strict: PASS only if the criterion is fully met right now'
        )
        response = await self._call_claw(prompt, read_only=True)
        return response.get('content', '')

    def _evaluate_success(self, assessment: str) -> bool:
        """
        Returns True only when the assessment contains no FAIL or PARTIAL
        verdicts and at least as many PASS verdicts as there are criteria.

        Uses structured CRITERION_N: label matching to avoid false positives
        from validation output or code that contains the words PASS/FAIL.
        Falls back to raw word counts only when no structured labels are found.
        """
        import re

        # Primary path: match the structured format the assess prompt produces.
        # CRITERION_N: PASS | ...
        # CRITERION_N: FAIL | ...
        # CRITERION_N: PARTIAL | ...
        pass_count = len(re.findall(
            r'^CRITERION_\d+:\s+PASS\b', assessment, re.MULTILINE | re.IGNORECASE
        ))
        fail_count = len(re.findall(
            r'^CRITERION_\d+:\s+FAIL\b', assessment, re.MULTILINE | re.IGNORECASE
        ))
        partial_count = len(re.findall(
            r'^CRITERION_\d+:\s+PARTIAL\b', assessment, re.MULTILINE | re.IGNORECASE
        ))

        # If structured labels were found, use them exclusively.
        if pass_count + fail_count + partial_count > 0:
            return (
                fail_count == 0
                and partial_count == 0
                and pass_count >= len(self.success_criteria)
            )

        # Fallback: unstructured assessment (model didn't follow the format).
        # Use word-boundary matching but be conservative — any FAIL or PARTIAL
        # word means we are not done.
        upper = assessment.upper()
        raw_fail    = len(re.findall(r'\bFAIL\b', upper))
        raw_partial = len(re.findall(r'\bPARTIAL\b', upper))
        raw_pass    = len(re.findall(r'\bPASS\b', upper))
        return (
            raw_fail == 0
            and raw_partial == 0
            and raw_pass >= len(self.success_criteria)
        )

    async def _plan_next_task(self, assessment: str) -> str:
        """
        Read-only CLAW call: given the current assessment, decide the single
        most important next task to make progress toward the goal.
        Returns empty string if there are no unmet criteria (all PASS).
        """
        # Extract only the FAIL/PARTIAL lines so the model focuses on gaps
        failing = [
            line for line in assessment.splitlines()
            if 'FAIL' in line.upper() or 'PARTIAL' in line.upper()
        ]

        # If no failing/partial lines, all criteria may already be met —
        # return empty string so the caller can re-check and exit the loop.
        if not failing:
            return ''

        history_text = self._format_history()
        gaps = '\n'.join(failing)
        prompt = (
            f'Goal: {self.goal}\n\n'
            f'Unmet criteria:\n{gaps}\n\n'
            f'Previous iterations:\n{history_text}\n\n'
            f'Respond with ONE concrete implementation task only — no headings, '
            f'no assessment, no PASS/FAIL labels.\n'
            f'Example format: "Add X to Y by doing Z"\n'
            f'Do NOT use read_file or search_code — just state the task as plain text.'
        )
        response = await self._call_claw(prompt, read_only=True)
        task = response.get('content', '').strip()
        return task if task else ''

    async def _call_claw(self, task: str, read_only: bool = False) -> dict:
        """
        Call CLAW with the given task.
        Uses direct agent call when an agent is available (in-process mode),
        falls back to HTTP when running as an external orchestrator.
        """
        if self._agent is not None:
            return await self._call_claw_direct(task, read_only)
        return await self._call_claw_http(task, read_only)

    async def _call_claw_direct(self, task: str, read_only: bool) -> dict:
        """
        Call the agent directly without HTTP (in-process mode).
        Retries up to 3 times on overload/rate-limit errors with backoff.
        """
        import asyncio as _asyncio
        from core.channels.envelope import MessageEnvelope, Channel

        max_tool_rounds = 3 if read_only else None
        auto_approve_review = (not read_only) and self.auto_approve_review

        envelope = MessageEnvelope(
            content=task,
            channel=Channel.WEB,
            project_id=self.project_id,
            session_id=self.session_id,
            read_only=read_only,
            max_tool_rounds=max_tool_rounds,
            auto_approve_review=auto_approve_review,
        )

        last_exc = None
        for attempt in range(3):
            try:
                response = await self._agent.process(envelope)
                return {
                    'content': response.content,
                    'pending_tool_call': response.pending_tool_call,
                    'model_used': response.model_used,
                    'tokens_used': response.tokens_used,
                    'cost_usd': response.cost_usd,
                    'tool_calls': response.executed_tool_calls,
                }
            except Exception as e:
                last_exc = e
                err_str = str(e).lower()
                # Retry on overload or rate limit; propagate anything else
                if 'overload' in err_str or '529' in err_str or '429' in err_str or 'rate' in err_str:
                    wait = 30 * (attempt + 1)
                    print(f'[wiggum] API overloaded, retrying in {wait}s (attempt {attempt+1}/3)')
                    await _asyncio.sleep(wait)
                else:
                    raise
        raise last_exc

    async def _call_claw_http(self, task: str, read_only: bool) -> dict:
        """POST to CLAW /chat for external orchestrator use."""
        import httpx

        payload = {
            'content': task,
            'project_id': self.project_id,
            'session_id': self.session_id,
            'channel': 'web',
            'read_only': read_only,
        }

        timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f'{self.api_url}/chat',
                json=payload,
                headers={'X-API-Key': self.api_key},
            )
            r.raise_for_status()
            return r.json()

    def _format_history(self) -> str:
        if not self.history:
            return 'No previous iterations.'
        lines = []
        for h in self.history[-3:]:  # Last 3 for brevity
            status = '⏳ pending approval' if h['has_pending_approval'] else '✓'
            lines.append(
                f"  [{h['iteration']}] {status} "
                f"Task: {h['task'][:80]}... "
                f"→ {h['result_summary'][:80]}..."
            )
        return '\n'.join(lines)
