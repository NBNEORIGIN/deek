"""
Cairn Social cost logging — wires Claude API call usage into Cairn's existing
/costs/log pipeline (per CLAUDE.md Step 4b + CAIRN_SOCIAL_V2_HANDOFF.md
correction A).

Best-effort: writes directly to MemoryStore.add_message and the cost_log.csv
file using the same schema /costs/log uses, so cost data flows into Cairn's
existing per-session spend tracking without going back through the HTTP
endpoint.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CAIRN_PROJECT = 'claw'


def log_social_cost(
    *,
    session_id: str,
    prompt_summary: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_gbp: float,
) -> None:
    """Log a single Claude API call to Cairn's cost tracking.

    This mirrors the /costs/log endpoint behaviour (api/main.py:1543) but
    in-process so we don't HTTP-call ourselves. Always best-effort — never
    raises.
    """
    now_iso = datetime.utcnow().isoformat() + 'Z'
    data_dir = os.getenv('CLAW_DATA_DIR', './data')

    # 1. SQLite via MemoryStore (same column piggyback /costs/log uses)
    try:
        from core.memory.store import MemoryStore
        store = MemoryStore(CAIRN_PROJECT, data_dir)
        try:
            store.add_message(
                session_id=session_id,
                role='assistant',
                content=f'[cost-log] {prompt_summary}',
                channel='cairn-social',
                model_used=model,
                tokens_used=tokens_in + tokens_out,
                cost_usd=cost_gbp,  # cost_usd column stores GBP per existing convention
            )
        finally:
            store.close()
    except Exception as exc:
        logger.warning('Cairn Social cost log (sqlite) failed: %s', exc)

    # 2. CSV append (best-effort, survives DB failures)
    try:
        csv_path = Path(data_dir) / 'cost_log.csv'
        csv_existed = csv_path.exists()
        with open(csv_path, 'a', encoding='utf-8') as f:
            if not csv_existed:
                f.write(
                    'timestamp,session_id,project,prompt_summary,'
                    'model,tokens_in,tokens_out,cost_gbp,total_cost_gbp\n'
                )
            # Sanitise commas in the prompt_summary so the CSV stays valid
            safe_summary = prompt_summary.replace(',', ';').replace('\n', ' ')
            f.write(
                f'{now_iso},{session_id},{CAIRN_PROJECT},{safe_summary},'
                f'{model},{tokens_in},{tokens_out},{cost_gbp},{cost_gbp}\n'
            )
    except Exception as exc:
        logger.warning('Cairn Social cost log (csv) failed: %s', exc)
