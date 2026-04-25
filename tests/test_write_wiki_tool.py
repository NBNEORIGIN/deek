"""Tests for core.tools.wiki_tools — write_wiki agent tool."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.tools.wiki_tools import _slugify, _write_wiki_draft


class TestSlugify:
    def test_basic(self):
        assert _slugify('Castle Mortgage SOP') == 'castle-mortgage-sop'

    def test_punctuation_collapsed(self):
        assert _slugify('What, now?!') == 'what-now'

    def test_empty(self):
        assert _slugify('') == 'untitled'
        assert _slugify(None) == 'untitled'

    def test_truncated(self):
        assert len(_slugify('a' * 200)) <= 60


class TestWriteWikiDraft:
    @pytest.fixture(autouse=True)
    def _isolate_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            'core.tools.wiki_tools._DRAFTS_DIR',
            tmp_path / 'wiki-drafts',
        )
        monkeypatch.setattr(
            'core.tools.wiki_tools._embed_into_chunks',
            lambda **k: (True, 'embedded'),
        )

    def test_happy_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            'core.tools.wiki_tools._DRAFTS_DIR',
            tmp_path / 'wiki-drafts',
        )
        out = _write_wiki_draft(
            '.', title='Castle Mortgage SOP',
            content='Step 1: collect docs.\nStep 2: review.',
            tags=['sop', 'castle'],
        )
        assert 'data/wiki-drafts/castle-mortgage-sop.md' in out
        assert 'indexed for search_wiki' in out
        # File exists with expected shape
        path = tmp_path / 'wiki-drafts' / 'castle-mortgage-sop.md'
        assert path.exists()
        body = path.read_text(encoding='utf-8')
        assert body.startswith('# Castle Mortgage SOP')
        assert 'Step 1: collect docs.' in body
        assert '_tags: sop, castle_' in body
        assert 'drafted by Deek' in body

    def test_missing_title(self):
        out = _write_wiki_draft('.', title='', content='x')
        assert 'title' in out.lower()

    def test_missing_content(self):
        out = _write_wiki_draft('.', title='X', content='')
        assert 'content' in out.lower()

    def test_too_long_content(self):
        out = _write_wiki_draft(
            '.', title='Big', content='x' * 70000,
        )
        assert 'exceeds max' in out

    def test_existing_heading_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            'core.tools.wiki_tools._DRAFTS_DIR',
            tmp_path / 'wiki-drafts',
        )
        out = _write_wiki_draft(
            '.', title='Already Has Heading',
            content='# Already Has Heading\n\nbody text',
        )
        assert 'already-has-heading' in out
        body = (tmp_path / 'wiki-drafts' / 'already-has-heading.md').read_text(
            encoding='utf-8',
        )
        # Should NOT have a duplicated heading
        assert body.count('# Already Has Heading') == 1

    def test_idempotent_on_identical_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            'core.tools.wiki_tools._DRAFTS_DIR',
            tmp_path / 'wiki-drafts',
        )
        # First write
        _write_wiki_draft(
            '.', title='Idempotent', content='Same body',
        )
        # Second identical write
        out = _write_wiki_draft(
            '.', title='Idempotent', content='Same body',
        )
        assert 'no-op' in out

    def test_collision_suffixes_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            'core.tools.wiki_tools._DRAFTS_DIR',
            tmp_path / 'wiki-drafts',
        )
        _write_wiki_draft(
            '.', title='Same Slug', content='First version',
        )
        # Different content, same title → suffix
        out = _write_wiki_draft(
            '.', title='Same Slug', content='Different version',
        )
        assert 'same-slug-2' in out
        assert (tmp_path / 'wiki-drafts' / 'same-slug-2.md').exists()

    def test_tags_as_string(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            'core.tools.wiki_tools._DRAFTS_DIR',
            tmp_path / 'wiki-drafts',
        )
        out = _write_wiki_draft(
            '.', title='Tags As CSV',
            content='body',
            tags='one,two, three',
        )
        path = tmp_path / 'wiki-drafts' / 'tags-as-csv.md'
        body = path.read_text(encoding='utf-8')
        assert '_tags: one, two, three_' in body

    def test_embed_failure_still_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            'core.tools.wiki_tools._DRAFTS_DIR',
            tmp_path / 'wiki-drafts',
        )
        monkeypatch.setattr(
            'core.tools.wiki_tools._embed_into_chunks',
            lambda **k: (False, 'no embedding model'),
        )
        out = _write_wiki_draft(
            '.', title='Embed Will Fail', content='body',
        )
        assert 'embed-will-fail' in out
        assert 'indexing failed' in out
        assert 'POST /admin/wiki-sync' in out
        # File should still exist
        assert (tmp_path / 'wiki-drafts' / 'embed-will-fail.md').exists()
