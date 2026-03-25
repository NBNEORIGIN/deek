"""
Session Summariser — appends key decisions to projects/{project}/core.md.

Triggered:
  - On session close (or 30 min inactivity)
  - After each completed WIGGUM run

Summarisation uses Tier 2 (DeepSeek) with a structured extraction prompt.
Output is append-only — existing core.md content is never modified.
Duplicate detection via difflib.SequenceMatcher prevents re-adding
similar bullets across sessions.
"""
import asyncio
import difflib
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_LOG_HEADER = '## Session Log'
_INACTIVITY_SECONDS = 30 * 60  # 30 minutes

_EXTRACTION_PROMPT = """\
You are summarising a developer session for a coding assistant's memory file.

Review the conversation below and extract:
1. Decisions made (things the developer chose to do or not do)
2. New patterns or conventions discovered
3. Gotchas, bugs, or non-obvious behaviours encountered
4. Architectural rules that should guide future work

Output ONLY bullet points. Format:
- <decision or pattern in ≤20 words>

Maximum 10 bullets. Do not include greetings, tool results, or implementation \
details — only durable insights that should influence future sessions.
If nothing significant was decided or discovered, output: NOTHING_SIGNIFICANT

Conversation:
{conversation}"""


class SessionSummariser:
    """
    Appends compact session summaries to the project's core.md.

    Usage:
        summariser = SessionSummariser(project_id='claw')
        await summariser.summarise(messages, session_id='abc')
    """

    SIMILARITY_THRESHOLD = 0.85

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._core_md_path = Path(f'projects/{project_id}/core.md')
        self._inactivity_timer: threading.Timer | None = None

    # ─── Public API ───────────────────────────────────────────────────────────

    async def summarise(
        self,
        messages: list[dict],
        session_id: str = '',
        wiggum_run_id: str = '',
    ) -> list[str]:
        """
        Summarise the session and append bullets to core.md.

        Args:
            messages:      Last N conversation messages (dicts with role/content).
            session_id:    Used for log labelling.
            wiggum_run_id: When set, labels the entry as a WIGGUM run summary.

        Returns list of bullets that were appended (empty if nothing significant).
        """
        if not messages:
            return []

        # Use at most the last 20 messages
        recent = messages[-20:]
        conversation = '\n'.join(
            f'[{m.get("role","?")}]: {m.get("content","")[:500]}'
            for m in recent
        )

        raw_output = await self._call_tier2(conversation)
        if not raw_output or 'NOTHING_SIGNIFICANT' in raw_output:
            logger.info(f'[summariser] nothing significant for {session_id or wiggum_run_id}')
            return []

        bullets = self._parse_bullets(raw_output)
        if not bullets:
            return []

        existing = self._read_core_md()
        bullets = self._deduplicate(bullets, existing)
        if not bullets:
            logger.info('[summariser] all bullets were duplicates — skipping')
            return []

        label = wiggum_run_id or session_id or 'unknown'
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        section = f'\n## Session {date_str} — {self.project_id} ({label})\n'
        section += '\n'.join(f'- {b}' for b in bullets) + '\n'

        self._append_to_core_md(section, existing)
        logger.info(f'[summariser] appended {len(bullets)} bullets to {self._core_md_path}')
        return bullets

    def schedule_on_inactivity(
        self,
        messages: list[dict],
        session_id: str = '',
    ):
        """
        Schedule summarisation to run after 30 minutes of inactivity.
        Calling this again resets the timer.
        """
        if self._inactivity_timer:
            self._inactivity_timer.cancel()

        def _run():
            asyncio.run(self.summarise(messages, session_id=session_id))

        self._inactivity_timer = threading.Timer(_INACTIVITY_SECONDS, _run)
        self._inactivity_timer.daemon = True
        self._inactivity_timer.start()

    def cancel_inactivity_timer(self):
        """Cancel any pending inactivity timer (e.g. on explicit session close)."""
        if self._inactivity_timer:
            self._inactivity_timer.cancel()
            self._inactivity_timer = None

    # ─── Private ──────────────────────────────────────────────────────────────

    async def _call_tier2(self, conversation: str) -> str:
        """
        Call DeepSeek (Tier 2) with the extraction prompt.
        Falls back to Claude if DeepSeek is not configured.
        """
        prompt = _EXTRACTION_PROMPT.format(conversation=conversation)
        deepseek_key = os.getenv('DEEPSEEK_API_KEY', '')
        anthropic_key = os.getenv('ANTHROPIC_API_KEY', '')

        if deepseek_key:
            try:
                from core.models.deepseek_client import DeepSeekClient
                client = DeepSeekClient(api_key=deepseek_key)
                text, _, _ = await client.chat(
                    system='You are a concise technical memory assistant.',
                    history=[],
                    message=prompt,
                )
                return text
            except Exception as exc:
                logger.warning(f'[summariser] DeepSeek failed: {exc} — trying Claude')

        if anthropic_key:
            try:
                from core.models.claude_client import ClaudeClient
                client = ClaudeClient(api_key=anthropic_key)
                text, _, _ = await client.chat(
                    system='You are a concise technical memory assistant.',
                    history=[],
                    message=prompt,
                )
                return text
            except Exception as exc:
                logger.error(f'[summariser] Claude also failed: {exc}')

        return ''

    def _parse_bullets(self, text: str) -> list[str]:
        """Extract bullet text from lines starting with - or *."""
        bullets = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(('- ', '* ', '• ')):
                bullet = line[2:].strip()
                # Truncate to 20 words
                words = bullet.split()
                if len(words) > 20:
                    bullet = ' '.join(words[:20]) + '…'
                if bullet:
                    bullets.append(bullet)
        return bullets[:10]  # Max 10 bullets

    def _deduplicate(self, new_bullets: list[str], existing_content: str) -> list[str]:
        """Remove bullets that are too similar to existing content."""
        # Extract existing bullets
        existing_bullets = [
            line.strip()[2:].strip()
            for line in existing_content.splitlines()
            if line.strip().startswith(('- ', '* ', '• '))
        ]
        if not existing_bullets:
            return new_bullets

        kept = []
        for bullet in new_bullets:
            is_dup = any(
                difflib.SequenceMatcher(None, bullet.lower(), ex.lower()).ratio()
                >= self.SIMILARITY_THRESHOLD
                for ex in existing_bullets
            )
            if not is_dup:
                kept.append(bullet)
        return kept

    def _read_core_md(self) -> str:
        if self._core_md_path.exists():
            return self._core_md_path.read_text(encoding='utf-8')
        return ''

    def _append_to_core_md(self, section: str, existing: str):
        """
        Append to the ## Session Log section, creating it if absent.
        Never modifies content above the Session Log section.
        """
        self._core_md_path.parent.mkdir(parents=True, exist_ok=True)

        if _SESSION_LOG_HEADER in existing:
            # Append after the existing session log header
            new_content = existing.rstrip('\n') + '\n' + section
        else:
            # Create the section at the end of the file
            new_content = (
                existing.rstrip('\n')
                + f'\n\n{_SESSION_LOG_HEADER}\n'
                + section
            )

        self._core_md_path.write_text(new_content, encoding='utf-8')
