"""
Wiki Compilation Pipeline.

Compiles wiki articles from live module data, CRM context, and product
information. Uses DeepSeek for product/blank articles and OpenRouter
for client articles that need nuanced email summarisation.

Module articles are written manually (or by Sonnet) — this compiler
handles the automated, data-driven article types.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CLAW_ROOT = Path(__file__).resolve().parents[2]
_WIKI_ROOT = _CLAW_ROOT / 'wiki'


class WikiCompiler:
    """Compiles wiki articles from live data sources."""

    PRODUCT_PROMPT = """You are compiling a wiki article for NBNE's business knowledge base.

Write a concise article about product {m_number} using this data:

Product: {m_number} — {description}
Blank: {blank_name}
Marketplaces: {marketplaces}
Content quality: {bullet_count} bullets, {image_count} images
Performance (30d): {sessions} sessions, {conversion_rate}% conversion, {units_sold} units
Ad performance: {ad_spend} spend, ACOS {acos}%
Margin: {gross_margin}%
Issues: {diagnosis_codes}
Recommendations: {recommendations}

Format:
# {m_number} — {short_description}

## Overview
2-3 sentences: what the product is, which blank it uses, where it sells.

## Performance
Table: marketplace, ASIN, sessions, conversion, health score.

## Issues
Current diagnosis codes with plain-English explanation.

## Recommendations
Prioritised action list.

## Related
Backlinks to blank page, marketplace pages, similar products using [[path]] format.

Rules:
- Write for a non-developer audience
- Use plain English, not jargon
- Include backlinks using [[path]] format
- Be concise — aim for 200-400 words
- Do not invent data — only use what is provided above"""

    CLIENT_PROMPT = """You are compiling a wiki article for NBNE's CRM knowledge base.

Write a concise client profile using this data:

Client: {name}
Company: {company}
Email history: {email_summary}
Projects: {projects}
Last contact: {last_contact}
Revenue: {revenue}
Notes: {notes}

Format:
# {name} — {company}

## Relationship Summary
2-3 sentences: who they are, how long we've worked together, what we do for them.

## Project History
Table: project, date, status, value.

## Recent Communications
Last 3-5 email exchanges summarised in one sentence each.

## Next Steps
Any pending actions, follow-ups, or opportunities.

## Related
Backlinks to relevant products, processes, or other clients using [[path]] format.

Rules:
- Write for Jo and Toby — they know the clients, this is a reference doc
- Never include sensitive information (bank details, personal data)
- Summarise email content, do not reproduce it
- Be concise �� aim for 150-300 words"""

    def __init__(self):
        self._deepseek_key = os.getenv('DEEPSEEK_API_KEY', '')
        self._openrouter_key = os.getenv('OPENROUTER_API_KEY', '')

    async def compile(self, scope: str = 'all') -> dict:
        """Run the compilation pipeline for the specified scope.

        Returns a summary of what was compiled.
        """
        start = time.monotonic()
        results = {
            'scope': scope,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'articles_compiled': 0,
            'articles_skipped': 0,
            'articles_failed': 0,
            'sections': {},
        }

        if scope in ('all', 'modules'):
            r = await self._compile_modules()
            results['sections']['modules'] = r
            results['articles_compiled'] += r.get('compiled', 0)
            results['articles_skipped'] += r.get('skipped', 0)

        if scope in ('all', 'products'):
            r = await self._compile_products()
            results['sections']['products'] = r
            results['articles_compiled'] += r.get('compiled', 0)
            results['articles_skipped'] += r.get('skipped', 0)
            results['articles_failed'] += r.get('failed', 0)

        if scope in ('all', 'clients'):
            r = await self._compile_clients()
            results['sections']['clients'] = r
            results['articles_compiled'] += r.get('compiled', 0)
            results['articles_skipped'] += r.get('skipped', 0)
            results['articles_failed'] += r.get('failed', 0)

        results['duration_seconds'] = round(time.monotonic() - start, 1)

        # Update metadata
        self._update_meta(results)

        # Re-embed compiled articles into pgvector
        embedded = await self._embed_wiki_articles()
        results['articles_embedded'] = embedded

        return results

    async def _compile_modules(self) -> dict:
        """Module articles are manual — just verify they exist."""
        modules_dir = _WIKI_ROOT / 'modules'
        expected = [
            'phloe', 'manufacture', 'ledger', 'cairn',
            'amazon-intelligence', 'etsy-intelligence', 'crm', 'render',
        ]
        existing = {f.stem for f in modules_dir.glob('*.md')}
        missing = [m for m in expected if m not in existing]

        return {
            'compiled': 0,
            'skipped': len(existing),
            'missing': missing,
            'note': 'Module articles are manual. Use Sonnet to update them.',
        }

    async def _compile_products(self) -> dict:
        """Compile product articles from Amazon Intelligence data."""
        if not self._deepseek_key:
            return {'compiled': 0, 'skipped': 0, 'failed': 0,
                    'error': 'DEEPSEEK_API_KEY not set'}

        products_dir = _WIKI_ROOT / 'products'
        products_dir.mkdir(exist_ok=True)

        # Fetch product data from AMI
        product_data = self._fetch_product_data()
        if not product_data:
            return {'compiled': 0, 'skipped': 0, 'failed': 0,
                    'note': 'No product data available from AMI'}

        compiled = 0
        failed = 0
        skipped = 0

        for product in product_data:
            m_number = product.get('m_number', '')
            if not m_number:
                continue

            article_path = products_dir / f'{m_number}.md'

            # Skip if recently compiled
            if article_path.exists():
                age_hours = (
                    time.time() - article_path.stat().st_mtime
                ) / 3600
                if age_hours < 168:  # 7 days
                    skipped += 1
                    continue

            try:
                content = await self._call_deepseek(
                    self.PRODUCT_PROMPT.format(**product)
                )
                article_path.write_text(content, encoding='utf-8')
                compiled += 1
            except Exception as exc:
                logger.error("Failed to compile %s: %s", m_number, exc)
                failed += 1

        return {'compiled': compiled, 'skipped': skipped, 'failed': failed}

    async def _compile_clients(self) -> dict:
        """Compile client articles from CRM data."""
        api_key = self._openrouter_key or self._deepseek_key
        if not api_key:
            return {'compiled': 0, 'skipped': 0, 'failed': 0,
                    'error': 'Neither OPENROUTER_API_KEY nor DEEPSEEK_API_KEY set'}

        clients_dir = _WIKI_ROOT / 'clients'
        clients_dir.mkdir(exist_ok=True)

        client_data = self._fetch_client_data()
        if not client_data:
            return {'compiled': 0, 'skipped': 0, 'failed': 0,
                    'note': 'No client data available from CRM'}

        compiled = 0
        failed = 0
        skipped = 0

        for client in client_data:
            slug = client.get('slug', '')
            if not slug:
                continue

            article_path = clients_dir / f'{slug}.md'
            if article_path.exists():
                age_hours = (
                    time.time() - article_path.stat().st_mtime
                ) / 3600
                if age_hours < 168:
                    skipped += 1
                    continue

            try:
                if self._openrouter_key:
                    content = await self._call_openrouter(
                        self.CLIENT_PROMPT.format(**client)
                    )
                else:
                    content = await self._call_deepseek(
                        self.CLIENT_PROMPT.format(**client)
                    )
                article_path.write_text(content, encoding='utf-8')
                compiled += 1
            except Exception as exc:
                logger.error("Failed to compile client %s: %s", slug, exc)
                failed += 1

        return {'compiled': compiled, 'skipped': skipped, 'failed': failed}

    def _fetch_product_data(self) -> list[dict]:
        """Fetch product data from Amazon Intelligence tables."""
        db_url = os.getenv('DATABASE_URL', '')
        if not db_url:
            return []

        try:
            import psycopg2
            conn = psycopg2.connect(db_url, connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT
                        s.m_number,
                        s.title AS description,
                        s.blank_name,
                        s.marketplace,
                        s.asin,
                        s.health_score,
                        s.sessions_30d,
                        s.conversion_rate_30d,
                        s.units_sold_30d,
                        s.bullet_count,
                        s.image_count,
                        s.diagnosis_codes
                    FROM ami_listing_snapshots s
                    WHERE s.m_number IS NOT NULL
                    ORDER BY s.m_number
                    LIMIT 500
                """)
                rows = cur.fetchall()
            conn.close()

            # Group by M-number
            products: dict[str, dict] = {}
            for row in rows:
                m = row[0]
                if m not in products:
                    products[m] = {
                        'm_number': m,
                        'description': row[1] or '',
                        'short_description': (row[1] or '')[:60],
                        'blank_name': row[2] or 'Unknown',
                        'marketplaces': '',
                        'sessions': 0,
                        'conversion_rate': 0,
                        'units_sold': 0,
                        'bullet_count': row[9] or 0,
                        'image_count': row[10] or 0,
                        'diagnosis_codes': '',
                        'recommendations': '',
                        'ad_spend': '0',
                        'acos': '0',
                        'gross_margin': '0',
                        '_mp_list': [],
                    }
                products[m]['_mp_list'].append(
                    f"{row[3]}: {row[4]} (health: {row[5]})"
                )
                products[m]['sessions'] += row[6] or 0
                products[m]['units_sold'] += row[8] or 0
                if row[7]:
                    products[m]['conversion_rate'] = row[7]
                if row[11]:
                    products[m]['diagnosis_codes'] = row[11]

            for p in products.values():
                p['marketplaces'] = ', '.join(p.pop('_mp_list', []))

            return list(products.values())

        except Exception as exc:
            logger.warning("Failed to fetch product data: %s", exc)
            return []

    def _fetch_client_data(self) -> list[dict]:
        """Fetch client data from CRM database."""
        crm_db_url = os.getenv(
            'CRM_DATABASE_URL',
            'postgresql://cairn:cairn_nbne_2026@192.168.1.228:5432/cairn_crm',
        )

        try:
            import psycopg2
            conn = psycopg2.connect(crm_db_url, connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id, name, company, email, phone,
                        notes, created_at, last_contact
                    FROM clients
                    ORDER BY last_contact DESC NULLS LAST
                    LIMIT 100
                """)
                rows = cur.fetchall()
            conn.close()

            clients = []
            for row in rows:
                slug = (row[1] or '').lower().replace(' ', '-')
                slug = ''.join(c for c in slug if c.isalnum() or c == '-')
                clients.append({
                    'slug': slug,
                    'name': row[1] or '',
                    'company': row[2] or '',
                    'email_summary': 'No email history available',
                    'projects': 'No projects on file',
                    'last_contact': str(row[7] or ''),
                    'revenue': '0',
                    'notes': row[5] or '',
                })
            return clients

        except Exception as exc:
            logger.warning("Failed to fetch client data: %s", exc)
            return []

    async def _call_deepseek(self, prompt: str) -> str:
        """Call DeepSeek API for article compilation."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                'https://api.deepseek.com/chat/completions',
                headers={
                    'Authorization': f'Bearer {self._deepseek_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'deepseek-chat',
                    'messages': [
                        {'role': 'system', 'content': 'You are a technical writer for NBNE. Write concise, accurate wiki articles.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    'max_tokens': 1500,
                    'temperature': 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content']

    async def _call_openrouter(self, prompt: str) -> str:
        """Call OpenRouter API for article compilation (client articles)."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {self._openrouter_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'meta-llama/llama-3-70b-instruct',
                    'messages': [
                        {'role': 'system', 'content': 'You are a technical writer for NBNE. Write concise, accurate wiki articles.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    'max_tokens': 1500,
                    'temperature': 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content']

    async def _embed_wiki_articles(self) -> int:
        """Embed all wiki markdown files into pgvector with chunk_type='wiki'."""
        db_url = os.getenv('DATABASE_URL', '')
        if not db_url:
            return 0

        try:
            import psycopg2
            from pgvector.psycopg2 import register_vector
            conn = psycopg2.connect(db_url, connect_timeout=5)
            register_vector(conn)
        except Exception as exc:
            logger.warning("Cannot connect to DB for wiki embedding: %s", exc)
            return 0

        # Get embedding function
        embed_fn = self._get_embed_fn()
        if not embed_fn:
            conn.close()
            return 0

        embedded = 0
        for md_file in _WIKI_ROOT.rglob('*.md'):
            if md_file.name == 'index.md':
                continue

            rel_path = str(md_file.relative_to(_CLAW_ROOT)).replace('\\', '/')
            content = md_file.read_text(encoding='utf-8')

            if not content.strip():
                continue

            # Compute content hash for deduplication
            import hashlib
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            # Check if already embedded with same hash
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT content_hash FROM claw_code_chunks
                       WHERE project_id = 'claw' AND file_path = %s AND chunk_type = 'wiki'""",
                    (rel_path,),
                )
                existing = cur.fetchone()
                if existing and existing[0] == content_hash:
                    continue

            # Generate embedding
            try:
                embedding = embed_fn(content[:6000])  # Stay within nomic limit
            except Exception as exc:
                logger.debug("Embedding failed for %s: %s", rel_path, exc)
                continue

            # Extract article name from first heading
            chunk_name = md_file.stem
            for line in content.split('\n'):
                if line.startswith('# '):
                    chunk_name = line[2:].strip()
                    break

            with conn.cursor() as cur:
                # Upsert: delete old, insert new
                cur.execute(
                    """DELETE FROM claw_code_chunks
                       WHERE project_id = 'claw' AND file_path = %s AND chunk_type = 'wiki'""",
                    (rel_path,),
                )
                cur.execute(
                    """INSERT INTO claw_code_chunks
                       (project_id, file_path, chunk_content, chunk_type, chunk_name,
                        content_hash, embedding, last_modified, indexed_at)
                       VALUES (%s, %s, %s, 'wiki', %s, %s, %s::vector, NOW(), NOW())""",
                    ('claw', rel_path, content, chunk_name, content_hash, embedding),
                )
            conn.commit()
            embedded += 1

        conn.close()
        return embedded

    def _get_embed_fn(self):
        """Get an embedding function.

        Tries in order:
          1. Ollama nomic-embed-text (local, free)
          2. OpenAI text-embedding-3-small via API (~£0.01/1M tokens)
          3. DeepSeek embedding via API (if available)

        Returns a callable(text) -> list[float] or None.
        """
        # 1. Try Ollama (local, free)
        try:
            from core.models.ollama_client import OllamaClient
            client = OllamaClient(
                base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434'),
                model='nomic-embed-text',
            )
            test = client.embed("test")
            if test and len(test) > 0:
                logger.info("Wiki embedding: using Ollama nomic-embed-text")
                return client.embed
        except Exception:
            pass

        # 2. Try OpenAI text-embedding-3-small (768-dim to match nomic)
        openai_key = os.getenv('OPENAI_API_KEY', '')
        if openai_key:
            try:
                import httpx

                def openai_embed(text: str) -> list[float]:
                    resp = httpx.post(
                        'https://api.openai.com/v1/embeddings',
                        headers={
                            'Authorization': f'Bearer {openai_key}',
                            'Content-Type': 'application/json',
                        },
                        json={
                            'model': 'text-embedding-3-small',
                            'input': text[:8000],
                            'dimensions': 768,
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    return resp.json()['data'][0]['embedding']

                # Test it
                test = openai_embed("test")
                if test and len(test) == 768:
                    logger.info("Wiki embedding: using OpenAI text-embedding-3-small (768-dim)")
                    return openai_embed
            except Exception as exc:
                logger.debug("OpenAI embedding unavailable: %s", exc)

        # 3. Try DeepSeek embedding
        if self._deepseek_key:
            try:
                import httpx

                def deepseek_embed(text: str) -> list[float]:
                    resp = httpx.post(
                        'https://api.deepseek.com/embeddings',
                        headers={
                            'Authorization': f'Bearer {self._deepseek_key}',
                            'Content-Type': 'application/json',
                        },
                        json={
                            'model': 'deepseek-chat',
                            'input': text[:8000],
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()['data'][0]['embedding']
                    # Pad/truncate to 768 dims to match pgvector schema
                    if len(data) < 768:
                        data.extend([0.0] * (768 - len(data)))
                    return data[:768]

                test = deepseek_embed("test")
                if test and len(test) == 768:
                    logger.info("Wiki embedding: using DeepSeek embedding (768-dim)")
                    return deepseek_embed
            except Exception as exc:
                logger.debug("DeepSeek embedding unavailable: %s", exc)

        logger.warning("No embedding provider available — wiki articles will not be embedded")
        return None

    def _update_meta(self, results: dict) -> None:
        """Update compilation metadata files."""
        meta_dir = _WIKI_ROOT / '_meta'
        meta_dir.mkdir(exist_ok=True)

        # Update last_compiled.json
        last_compiled_path = meta_dir / 'last_compiled.json'
        try:
            last_compiled = json.loads(last_compiled_path.read_text(encoding='utf-8'))
        except Exception:
            last_compiled = {}

        now = datetime.now(timezone.utc).isoformat()
        for section in results.get('sections', {}):
            last_compiled[section] = now
        last_compiled_path.write_text(
            json.dumps(last_compiled, indent=2),
            encoding='utf-8',
        )

        # Append to compilation_log.json
        log_path = meta_dir / 'compilation_log.json'
        try:
            logs = json.loads(log_path.read_text(encoding='utf-8'))
        except Exception:
            logs = []

        logs.append({
            'run_id': now.replace(':', '-'),
            'scope': results.get('scope', 'unknown'),
            'articles_compiled': results.get('articles_compiled', 0),
            'articles_skipped': results.get('articles_skipped', 0),
            'articles_failed': results.get('articles_failed', 0),
            'duration_seconds': results.get('duration_seconds', 0),
            'timestamp': now,
        })
        # Keep last 50 entries
        logs = logs[-50:]
        log_path.write_text(json.dumps(logs, indent=2), encoding='utf-8')
