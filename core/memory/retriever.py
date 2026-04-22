from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import re
import time
from typing import Callable

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - exercised via availability checks
    class BM25Okapi:  # type: ignore[no-redef]
        """
        Tiny fallback scorer used when rank_bm25 is unavailable.

        This keeps hybrid retrieval working in degraded mode by ranking chunks
        with simple token-overlap scoring instead of disabling BM25 entirely.
        """

        def __init__(self, corpus: list[list[str]]):
            self.corpus = corpus

        def get_scores(self, query_tokens: list[str]) -> list[float]:
            query_set = set(query_tokens)
            scores: list[float] = []
            for doc_tokens in self.corpus:
                doc_set = set(doc_tokens)
                overlap = len(query_set & doc_set)
                length_penalty = max(len(doc_set), 1)
                scores.append(overlap / length_penalty)
            return scores


logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    file: str
    content: str
    chunk_type: str
    score: float = 0.0
    bm25_rank: int | None = None
    cosine_rank: int | None = None
    chunk_name: str | None = None

    @property
    def is_wiki(self) -> bool:
        return self.chunk_type == 'wiki'

    @property
    def match_quality(self) -> str:
        if self.bm25_rank is not None and self.cosine_rank is not None:
            return 'exact+semantic'
        if self.bm25_rank is not None:
            return 'exact'
        return 'semantic'

    @property
    def dedupe_key(self) -> str:
        head = self.content[:120]
        return f'{self.file}:{self.chunk_type}:{head}'

    @property
    def wiki_entity(self) -> str | None:
        """Extract the entity identifier from a wiki article path.

        For example, 'wiki/modules/phloe.md' returns 'phloe'.
        Returns None for non-wiki chunks.
        """
        if not self.is_wiki:
            return None
        # wiki/modules/phloe.md -> phloe
        name = self.file.rsplit('/', 1)[-1]
        if name.endswith('.md'):
            name = name[:-3]
        return name.lower()


_MEM_TYPES = frozenset({
    'memory', 'email', 'wiki', 'module_snapshot', 'social_post',
})


class HybridRetriever:
    """
    Hybrid Tier 2 retrieval for the current ContextEngine contract.

    BM25 runs over the existing pgvector chunk corpus already stored in
    `claw_code_chunks`. Results are merged with cosine search via
    Reciprocal Rank Fusion.

    The cache is short-lived rather than watcher-driven in Phase 1 so the
    implementation stays compatible with the current watcher wiring.
    """

    CACHE_TTL_SECONDS = 30.0

    def __init__(
        self,
        context_engine,
        bm25_top_k: int = 20,
        cosine_top_k: int = 20,
        rrf_k: int = 60,
    ):
        self.context_engine = context_engine
        self.bm25_top_k = bm25_top_k
        self.cosine_top_k = cosine_top_k
        self.rrf_k = rrf_k
        self._bm25_cache: dict[str, BM25Okapi] = {}
        self._bm25_corpus: dict[str, list[RetrievedChunk]] = {}
        self._bm25_built_at: dict[str, float] = {}

    @property
    def is_available(self) -> bool:
        return bool(self.context_engine.db_url)

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        tokens = re.findall(r'[a-z0-9_./\-]+', text)
        return [tok for tok in tokens if len(tok) > 1]

    def _cache_key(self, subproject_id: str | None) -> str:
        if not subproject_id:
            return f'{self.context_engine.project_id}:global'
        if subproject_id.startswith(f'{self.context_engine.project_id}:'):
            return subproject_id
        return f'{self.context_engine.project_id}:{subproject_id}'

    def _cache_is_fresh(self, key: str) -> bool:
        built_at = self._bm25_built_at.get(key)
        if built_at is None:
            return False
        return (time.monotonic() - built_at) < self.CACHE_TTL_SECONDS

    def _build_bm25_index(
        self,
        subproject_id: str | None = None,
    ) -> tuple[BM25Okapi | None, list[RetrievedChunk]]:
        raw_chunks = self.context_engine.get_all_chunks(subproject_id=subproject_id)
        chunks = [
            RetrievedChunk(
                file=chunk['file'],
                content=chunk['content'],
                chunk_type=chunk.get('chunk_type', 'window'),
                chunk_name=chunk.get('chunk_name'),
            )
            for chunk in raw_chunks
        ]
        if not chunks:
            return None, []

        corpus = [
            self._tokenize(
                ' '.join(
                    part for part in (
                        chunk.file,
                        chunk.chunk_name or '',
                        chunk.content,
                    )
                    if part
                )
            )
            for chunk in chunks
        ]
        if not any(corpus):
            return None, []
        return BM25Okapi(corpus), chunks

    def _get_or_build_bm25(
        self,
        subproject_id: str | None = None,
    ) -> tuple[BM25Okapi | None, list[RetrievedChunk]]:
        key = self._cache_key(subproject_id)
        if key not in self._bm25_cache or not self._cache_is_fresh(key):
            index, corpus = self._build_bm25_index(subproject_id=subproject_id)
            if index is None:
                self._bm25_cache.pop(key, None)
                self._bm25_corpus.pop(key, None)
                self._bm25_built_at.pop(key, None)
                return None, []
            self._bm25_cache[key] = index
            self._bm25_corpus[key] = corpus
            self._bm25_built_at[key] = time.monotonic()
        return self._bm25_cache.get(key), self._bm25_corpus.get(key, [])

    def invalidate_cache(self, subproject_id: str | None = None) -> None:
        if subproject_id is None:
            prefix = f'{self.context_engine.project_id}:'
            for key in list(self._bm25_cache):
                if key.startswith(prefix):
                    self._bm25_cache.pop(key, None)
                    self._bm25_corpus.pop(key, None)
                    self._bm25_built_at.pop(key, None)
            return

        key = self._cache_key(subproject_id)
        self._bm25_cache.pop(key, None)
        self._bm25_corpus.pop(key, None)
        self._bm25_built_at.pop(key, None)

    def _bm25_search(
        self,
        query: str,
        subproject_id: str | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        top_k = top_k or self.bm25_top_k
        index, corpus = self._get_or_build_bm25(subproject_id=subproject_id)
        if index is None or not corpus:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = index.get_scores(query_tokens)
        top_indices = sorted(
            range(len(scores)),
            key=lambda idx: scores[idx],
            reverse=True,
        )[:top_k]

        results: list[RetrievedChunk] = []
        for rank, idx in enumerate(top_indices):
            score = float(scores[idx])
            doc_tokens = self._tokenize(
                ' '.join(
                    part for part in (
                        corpus[idx].file,
                        corpus[idx].chunk_name or '',
                        corpus[idx].content,
                    )
                    if part
                )
            )
            overlap = len(set(query_tokens) & set(doc_tokens))
            if score <= 0 and overlap == 0:
                continue
            if score <= 0 and overlap > 0:
                score = overlap / max(len(set(query_tokens)), 1)
            results.append(
                replace(
                    corpus[idx],
                    score=score,
                    bm25_rank=rank,
                    cosine_rank=None,
                )
            )
        return results

    # Memory-bearing chunk types (Brief 2 Phase A) — only these receive
    # salience-based reranking and reinforcement. Keeping the set local
    # avoids an import cycle with core.memory.salience at module load.
    # Must match salience.MEMORY_CHUNK_TYPES.

    def _to_chunk(self, row: dict, cosine_rank: int | None = None) -> RetrievedChunk:
        return RetrievedChunk(
            file=row['file'],
            content=row['content'],
            chunk_type=row.get('chunk_type', 'window'),
            chunk_name=row.get('chunk_name'),
            score=float(row.get('score', 0.0)),
            bm25_rank=None,
            cosine_rank=cosine_rank,
        )

    def _rrf_merge(
        self,
        bm25_results: list[RetrievedChunk],
        cosine_results: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        scores: dict[str, float] = {}
        merged: dict[str, RetrievedChunk] = {}

        for rank, chunk in enumerate(bm25_results):
            key = chunk.dedupe_key
            scores[key] = scores.get(key, 0.0) + 1.0 / (self.rrf_k + rank + 1)
            merged[key] = replace(chunk, bm25_rank=rank)

        for rank, chunk in enumerate(cosine_results):
            key = chunk.dedupe_key
            scores[key] = scores.get(key, 0.0) + 1.0 / (self.rrf_k + rank + 1)
            if key in merged:
                merged[key] = replace(
                    merged[key],
                    cosine_rank=rank,
                    score=float(scores[key]),
                )
            else:
                merged[key] = replace(
                    chunk,
                    cosine_rank=rank,
                    score=float(scores[key]),
                )

        ordered = sorted(scores, key=lambda key: scores[key], reverse=True)
        return [
            replace(merged[key], score=float(scores[key]))
            for key in ordered
        ]

    # ── Wiki boost configuration ──────────────────────────────────
    WIKI_BOOST_FACTOR = 1.5
    WIKI_BACKLINK_BUDGET = 3
    WIKI_GUARANTEED_K = 3   # always inject this many wiki results, bypass threshold

    def _wiki_search(
        self,
        query: str,
        embedding_fn: Callable,
        top_k: int | None = None,
        extra_project_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Dedicated wiki-only cosine search with NO similarity threshold.

        This guarantees that relevant wiki articles surface even when they score
        below the global SIMILARITY_THRESHOLD (0.65). Called separately from the
        main cosine search so wiki knowledge is never crowded out by email/code chunks.

        extra_project_ids: additional projects to search for wiki chunks (e.g. 'deek'
        chunks surfaced in the 'nbne' business interface). Configured via
        wiki_source_projects in the project's config.json.
        """
        top_k = top_k or self.WIKI_GUARANTEED_K
        project_ids = [self.context_engine.project_id] + (extra_project_ids or [])
        # deduplicate while preserving order
        seen: set[str] = set()
        unique_pids: list[str] = []
        for pid in project_ids:
            if pid not in seen:
                seen.add(pid)
                unique_pids.append(pid)

        placeholders = ', '.join(['%s'] * len(unique_pids))
        try:
            query_embedding = embedding_fn(query)
            conn = self.context_engine._get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT file_path, chunk_content, chunk_type, chunk_name,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM claw_code_chunks
                    WHERE project_id IN ({placeholders})
                      AND chunk_type = 'wiki'
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        query_embedding,
                        *unique_pids,
                        query_embedding,
                        top_k,
                    ),
                )
                rows = cur.fetchall()
            # Defend against NULL similarity — happens when the embedder
            # returns None for an empty query or when pgvector can't
            # compute distance against a NULL vector.
            return [
                RetrievedChunk(
                    file=row[0],
                    content=row[1],
                    chunk_type=row[2],
                    chunk_name=row[3],
                    score=float(row[4]) if row[4] is not None else 0.0,
                    cosine_rank=rank,
                )
                for rank, row in enumerate(rows)
            ]
        except Exception as exc:
            logger.warning('[retriever] wiki_search failed: %s', exc)
            return []

    def retrieve(
        self,
        task: str,
        embedding_fn: Callable,
        subproject_id: str | None = None,
    ) -> list[dict]:
        if not self.is_available:
            raise RuntimeError('Hybrid retrieval unavailable')

        bm25_results = self._bm25_search(task, subproject_id=subproject_id)

        cosine_error = None
        try:
            cosine_raw = self.context_engine._retrieve_by_embedding(
                task,
                embedding_fn,
                subproject_id=subproject_id,
                limit=self.cosine_top_k,
            )
        except Exception as exc:
            cosine_error = exc
            cosine_raw = []

        cosine_results = [
            self._to_chunk(row, cosine_rank=rank)
            for rank, row in enumerate(cosine_raw)
        ]

        if bm25_results and cosine_results:
            merged = self._rrf_merge(bm25_results, cosine_results)
        elif bm25_results:
            merged = bm25_results
        elif cosine_results:
            merged = cosine_results
        else:
            if cosine_error:
                logger.warning(
                    '[retriever] cosine retrieval failed for %s: %s',
                    self.context_engine.project_id,
                    cosine_error,
                )
            # Don't early-return here — wiki injection may still surface results
            # from cross-project wiki_source_projects even when this project is empty.
            merged = list(self.context_engine._retrieve_by_keyword(task, subproject_id))

        # ── Guaranteed wiki injection ─────────────────────────────────────────
        # Wiki chunks that scored below SIMILARITY_THRESHOLD are excluded from
        # cosine_results before they reach the boost step. Run a dedicated
        # no-threshold wiki search and inject any wiki chunks not already present.
        # Also searches wiki_source_projects so business projects (e.g. 'nbne')
        # can pull wiki articles stored under the developer project ('deek').
        extra_wiki_pids: list[str] = self.context_engine._load_config().get(
            'wiki_source_projects', []
        )
        guaranteed_wiki = self._wiki_search(
            task, embedding_fn, extra_project_ids=extra_wiki_pids
        )
        if guaranteed_wiki:
            existing_files = {c.dedupe_key for c in merged}
            for wiki_chunk in guaranteed_wiki:
                if wiki_chunk.dedupe_key not in existing_files:
                    merged.append(wiki_chunk)
                    existing_files.add(wiki_chunk.dedupe_key)

        if not merged:
            return []

        boosted = self._apply_wiki_boost(merged)
        top_k = self.context_engine.MAX_TIER2_CHUNKS
        final = self._follow_backlinks(boosted[:top_k])

        # ── Graph walk (Brief 3 Phase B) ────────────────────────────
        # Extract query entities, walk 1-2 hops, surface memories
        # linked to visited entities. Shadow-gated by
        # DEEK_CROSSLINK_SHADOW — under shadow we log the diff but
        # don't touch the returned result.
        graph_candidates = []
        try:
            from core.memory.graph_walk import walk_for_query
            graph_candidates = walk_for_query(task)
        except Exception as exc:
            logger.debug('[retriever] graph walk failed: %s', exc)

        # ── Schema retrieval (Brief 2 Phase B, Task 7) ──────────────
        # Strategic queries also pull top-K schemas from the consolidation
        # table. Schema hits are attached as a separate list on the
        # returned chunks so Deek can cite them distinctly.
        schema_hits: list[dict] = []
        try:
            from core.memory.schema_retrieval import (
                is_strategic_query, retrieve_schemas,
                reinforce_schemas_async,
            )
            if is_strategic_query(task):
                schema_hits = retrieve_schemas(task, embedding_fn, top_k=3)
                if schema_hits:
                    reinforce_schemas_async([s['id'] for s in schema_hits])
        except Exception as exc:
            logger.debug('[retriever] schema retrieval failed: %s', exc)

        # ── Impressions layer (Brief 2 Phase A) ─────────────────────
        # Attach salience/recency/chunk_id to each candidate, rerank,
        # and fire async reinforcement on the memory-bearing hits.
        # Shadow-mode gated — under DEEK_IMPRESSIONS_SHADOW=true (the
        # default) we compute the new ordering and log the diff but
        # return the existing ordering. Setting the env to 'false'
        # after a week of reviewed shadow data flips to live.
        old_dicts = [self._to_dict(c) for c in final[:top_k]]
        try:
            decorated = self._attach_impressions_fields(old_dicts)
            from core.memory.impressions import (
                rerank, shadow_enabled, log_shadow_comparison,
                reinforce_async,
            )
            rrf_scores = [c.get('score', 0.0) for c in decorated]
            new_order, debug = rerank(decorated, rrf_scores=rrf_scores)
            # Reinforce memory-bearing chunks we're about to return.
            mem_ids = [
                c['chunk_id'] for c in decorated
                if c.get('chunk_id') is not None
                and c.get('chunk_type') in _MEM_TYPES
            ]
            if mem_ids:
                reinforce_async(mem_ids)
            if shadow_enabled():
                log_shadow_comparison(task, old_dicts, new_order, debug)
                result = old_dicts
            else:
                result = new_order

            # ── Graph fusion (Brief 3 Phase B) ─────────────────────
            # Shadow-mode gated separately from impressions shadow so
            # we can cut over the two layers independently.
            try:
                from core.memory.graph_walk import (
                    shadow_enabled as gw_shadow, fuse_into, log_shadow,
                )
                if gw_shadow():
                    if graph_candidates:
                        log_shadow(task, result, graph_candidates)
                    # Shadow returns result unchanged.
                else:
                    result = fuse_into(result, graph_candidates)
            except Exception as exc:
                logger.debug('[retriever] graph fuse failed: %s', exc)

            # Attach schema hits as a trailing section so existing
            # callers that just iterate the list see them, but the
            # metadata (chunk_type='schema') makes them filterable.
            for s in schema_hits:
                result.append({
                    'file': f'schema/{s["id"]}',
                    'content': s['statement'],
                    'chunk_type': 'schema',
                    'chunk_name': s['statement'][:80],
                    'score': s['boosted_score'],
                    'match_quality': 'schema',
                    'schema_id': s['id'],
                    'schema_confidence': s['confidence'],
                    'schema_source_memory_ids': s['source_memory_ids'],
                    'similarity': s['similarity'],
                })
            return result
        except Exception as exc:
            logger.debug(
                '[retriever] impressions rerank failed, serving old order: %s',
                exc,
            )
            return old_dicts

    def _attach_impressions_fields(self, chunks: list[dict]) -> list[dict]:
        """Batch-fetch chunk_id / salience / last_accessed_at / access_count.

        The retrieval SQL in the hybrid path and ``engine.py`` don't
        currently SELECT these columns, and changing every SELECT is
        out of scope for Phase A. Instead we do one lookup per chunk
        keyed on (project_id, file_path, chunk_type) — enough to
        disambiguate memory entries which are all
        project/file_path-unique. For code chunks the salience stays
        at its default 1.0.
        """
        if not chunks:
            return chunks
        try:
            conn = self.context_engine._get_connection()
        except Exception as exc:
            logger.debug(
                '[retriever] impressions: cannot open conn: %s', exc,
            )
            return chunks
        try:
            triples = [
                (self.context_engine.project_id, c.get('file', ''),
                 c.get('chunk_type', ''))
                for c in chunks
            ]
            # One query, VALUES-joined — fast even at top_k=20.
            # Also selects salience_signals so the downstream rerank
            # can read toby_flag / via / triage_id and boost chunks
            # accordingly. Before 2026-04-22 this JSONB column was
            # write-only; migration 0010 added the partial indexes.
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH needle(project_id, file_path, chunk_type) AS (
                        SELECT * FROM unnest(
                            %s::text[], %s::text[], %s::text[]
                        )
                    )
                    SELECT n.file_path, n.chunk_type,
                           c.id, c.salience, c.last_accessed_at,
                           c.access_count, c.salience_signals
                      FROM needle n
                      LEFT JOIN LATERAL (
                          SELECT id, salience, last_accessed_at,
                                 access_count, salience_signals
                            FROM claw_code_chunks
                           WHERE project_id = n.project_id
                             AND file_path  = n.file_path
                             AND chunk_type = n.chunk_type
                           ORDER BY indexed_at DESC
                           LIMIT 1
                      ) c ON TRUE
                    """,
                    (
                        [t[0] for t in triples],
                        [t[1] for t in triples],
                        [t[2] for t in triples],
                    ),
                )
                rows = cur.fetchall()
            lookup = {(r[0], r[1]): r for r in rows}
            decorated: list[dict] = []
            for c in chunks:
                key = (c.get('file', ''), c.get('chunk_type', ''))
                r = lookup.get(key)
                d = dict(c)
                if r is not None and r[2] is not None:
                    d['chunk_id'] = r[2]
                    d['salience'] = float(r[3] or 1.0)
                    d['last_accessed_at'] = r[4]
                    d['access_count'] = int(r[5] or 0)
                    sig = r[6]
                    if isinstance(sig, str):
                        try:
                            sig = json.loads(sig)
                        except Exception:
                            sig = {}
                    d['salience_signals'] = sig or {}
                else:
                    d.setdefault('chunk_id', None)
                    d.setdefault('salience', 1.0)
                    d.setdefault('last_accessed_at', None)
                    d.setdefault('access_count', 0)
                    d.setdefault('salience_signals', {})
                d.setdefault('dedupe_key', f"{c.get('file')}:{c.get('chunk_type')}")
                decorated.append(d)
            return decorated
        except Exception as exc:
            logger.debug(
                '[retriever] impressions: decorate failed: %s', exc,
            )
            return chunks

    # ── Wiki layer methods ─────────────────────────────────────

    def _apply_wiki_boost(
        self, results: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """Apply score boost to wiki chunks and deduplicate.

        Wiki articles get a 1.5x score multiplier. When a wiki article
        and a raw chunk cover the same entity (matched by file-path
        entity extraction), the wiki article wins and the raw chunk
        is dropped.

        Safe for projects with no wiki articles — the loop simply finds
        nothing to boost and returns the list re-sorted.
        """
        # Boost wiki scores
        boosted: list[RetrievedChunk] = []
        for chunk in results:
            if chunk.is_wiki:
                boosted.append(replace(chunk, score=chunk.score * self.WIKI_BOOST_FACTOR))
            else:
                boosted.append(chunk)

        # Re-sort by boosted score
        boosted.sort(key=lambda c: c.score, reverse=True)

        # Deduplicate: wiki wins over raw for the same entity
        seen_entities: set[str] = set()
        deduped: list[RetrievedChunk] = []
        for chunk in boosted:
            entity = chunk.wiki_entity
            if entity:
                # This is a wiki chunk — claim the entity
                seen_entities.add(entity)
                deduped.append(chunk)
            else:
                # Raw chunk — check if a wiki article already covers this entity
                raw_entity = self._extract_entity_from_raw(chunk)
                if raw_entity and raw_entity in seen_entities:
                    continue  # wiki already covers this
                deduped.append(chunk)

        return deduped

    @staticmethod
    def _extract_entity_from_raw(chunk: RetrievedChunk) -> str | None:
        """Try to extract a module/entity name from a raw chunk's file path.

        Maps paths like 'core/etsy_intel/sync.py' to 'etsy-intelligence',
        or 'projects/phloe/core.md' to 'phloe'. Returns None if no
        meaningful entity can be extracted.
        """
        path = chunk.file.lower().replace('\\', '/')
        # Direct project paths
        if path.startswith('projects/') and '/' in path[len('projects/'):]:
            return path.split('/')[1]
        # Module code paths
        module_map = {
            'core/amazon_intel': 'amazon-intelligence',
            'core/etsy_intel': 'etsy-intelligence',
            'api/routes/amazon_intel': 'amazon-intelligence',
            'api/routes/etsy_intel': 'etsy-intelligence',
        }
        for prefix, entity in module_map.items():
            if path.startswith(prefix):
                return entity
        return None

    def _follow_backlinks(
        self, results: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """Follow [[backlinks]] from wiki articles, one level deep.

        For each wiki article in the results, extract [[path]] links
        and load the linked wiki articles from the database. Adds up
        to WIKI_BACKLINK_BUDGET linked articles to the results.

        Safe for projects with no wiki articles — finds no backlinks.
        """
        budget = self.WIKI_BACKLINK_BUDGET
        if budget <= 0:
            return results

        seen_files = {chunk.file for chunk in results}
        extra: list[RetrievedChunk] = []

        for chunk in results:
            if budget <= 0:
                break
            if not chunk.is_wiki:
                continue
            links = self._extract_backlinks(chunk.content)
            for link in links:
                if budget <= 0:
                    break
                # Normalise link to file_path format
                file_path = link if link.endswith('.md') else f'{link}.md'
                if not file_path.startswith('wiki/'):
                    file_path = f'wiki/{file_path}'
                if file_path in seen_files:
                    continue
                linked = self._load_wiki_chunk(file_path)
                if linked:
                    extra.append(linked)
                    seen_files.add(file_path)
                    budget -= 1

        return results + extra

    @staticmethod
    def _extract_backlinks(content: str) -> list[str]:
        """Extract [[wiki/path]] links from markdown content."""
        return re.findall(r'\[\[([^\]]+)\]\]', content)

    def _load_wiki_chunk(self, file_path: str) -> RetrievedChunk | None:
        """Load a single wiki article from the database by file_path."""
        try:
            conn = self.context_engine._get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT file_path, chunk_content, chunk_type, chunk_name
                    FROM claw_code_chunks
                    WHERE project_id = %s
                      AND chunk_type = 'wiki'
                      AND file_path = %s
                    LIMIT 1
                    """,
                    (self.context_engine.project_id, file_path),
                )
                row = cur.fetchone()
            if not row:
                return None
            return RetrievedChunk(
                file=row[0],
                content=row[1],
                chunk_type=row[2],
                chunk_name=row[3],
                score=0.5,  # backlinked articles get a neutral score
            )
        except Exception as exc:
            logger.debug(
                '[retriever] backlink load failed for %s: %s',
                file_path,
                exc,
            )
            return None

    def _to_dict(self, chunk: RetrievedChunk) -> dict:
        return {
            'file': chunk.file,
            'content': chunk.content,
            'chunk_type': chunk.chunk_type,
            'chunk_name': chunk.chunk_name,
            'score': float(chunk.score),
            'match_quality': chunk.match_quality,
            'bm25_rank': chunk.bm25_rank,
            'cosine_rank': chunk.cosine_rank,
        }
