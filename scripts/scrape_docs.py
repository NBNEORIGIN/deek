"""
scrape_docs.py — fetch web-based machine docs, extract clean text,
                 stage as markdown for the manuals upload pipeline.

Some machines have no PDF manuals — only an online doc site or a Zoho
Desk / Help Scout / GitBook knowledge base. This script crawls a
same-host site from a starting URL, extracts the main content of each
page via trafilatura, and writes one .md per page to a staging dir
under <output_dir>/<machine>/. The existing
scripts/upload_manuals_from_drive.py then ingests the staging dir
through the same /api/deek/manuals/upload pipeline, tagged by machine.

Why a separate scraper script (vs adding URL-fetch to ingest_manuals
or the upload route): scraping is its own concern with rate limiting,
robots.txt compliance, link discovery, and dedupe. Bolting it onto an
upload endpoint would either block the request for 10+ minutes or
introduce queue/worker infrastructure we don't need yet. As a CLI
tool that produces files on disk, the output composes cleanly with
the existing batch-upload path.

Polite crawling defaults:
  - 1 request per second per host (overridable with --rps)
  - User-Agent identifies us so site owners can complain
  - Respects robots.txt unless --ignore-robots
  - Same-host only (no following off-site links)
  - Hard cap of 1000 pages per run unless --max-pages overridden

Usage:
  # Dry-run a small slice to see what URLs would be crawled
  python scripts/scrape_docs.py \\
    --start-url https://docs.lightburnsoftware.com/ \\
    --machine Beast \\
    --output-dir .tmp/scrape-staging \\
    --max-pages 10 \\
    --dry-run

  # Real scrape (writes markdown files; does NOT upload)
  python scripts/scrape_docs.py \\
    --start-url https://docs.lightburnsoftware.com/ \\
    --machine Beast \\
    --output-dir .tmp/scrape-staging

  # Then upload via the existing pipeline:
  python scripts/upload_manuals_from_drive.py \\
    --folder .tmp/scrape-staging \\
    --commit
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / '.env')
except ImportError:
    pass

import httpx

USER_AGENT = 'NBNE-Deek/1.0 (+toby@nbnesigns.com)'

logger = logging.getLogger('scrape_docs')


# ── URL helpers ────────────────────────────────────────────────────


def _normalise_url(url: str, base: str | None = None) -> str:
    """Resolve relative URLs, drop fragments, strip tracking params.
    Site-specific tracking params we don't care to keep across runs:
    `gclid`, `fbclid`, `utm_*`."""
    if base:
        url = urllib.parse.urljoin(base, url)
    parsed = urllib.parse.urlparse(url)
    # Drop fragment
    parsed = parsed._replace(fragment='')
    # Filter tracking params
    if parsed.query:
        kept = [
            (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
            if not (k.startswith('utm_') or k in ('gclid', 'fbclid'))
        ]
        parsed = parsed._replace(
            query=urllib.parse.urlencode(kept) if kept else ''
        )
    return urllib.parse.urlunparse(parsed)


def _same_host(url: str, host: str) -> bool:
    p = urllib.parse.urlparse(url)
    return p.netloc.lower() == host.lower()


def _slug_for_url(url: str, host: str) -> str:
    """Filename for the markdown copy of a URL. Use the path slug,
    sanitised. Two URLs that differ only in query string get
    different slugs."""
    p = urllib.parse.urlparse(url)
    path = p.path.strip('/')
    if p.query:
        path = f'{path}__{p.query}'
    if not path:
        path = '_root'
    # Replace path separators + chars that don't belong in filenames
    slug = re.sub(r'[\\/\x00:*?<>|"]', '_', path)
    # Cap length
    return slug[:180] or '_root'


def _looks_like_html(url: str) -> bool:
    """Skip URLs that are obviously not HTML pages — assets (CSS, JS,
    fonts, icons), binary downloads (PDFs, zips, images), etc. PDFs
    and images for ingestion get a separate path; those are caught
    here too because the manuals upload UI handles them, not this
    scraper.

    Tested 2026-04-30: an earlier version omitted .css/.ico/.js and
    burned 9 of 10 fetches on MkDocs Material assets before reaching
    the first real article. Lesson: any extension a browser would
    treat as not-document goes here.
    """
    p = urllib.parse.urlparse(url)
    path = p.path.lower()
    bad_exts = (
        # Documents (handled by manuals UI directly, not this scraper)
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        # Archives
        '.zip', '.tar', '.gz', '.rar', '.7z',
        # Images
        '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico',
        # Fonts
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        # Stylesheets / scripts / data
        '.css', '.js', '.mjs', '.map', '.json', '.xml', '.yml', '.yaml',
        # Media
        '.mp4', '.webm', '.mov', '.avi', '.mkv',
        '.mp3', '.wav', '.ogg', '.flac',
        # Executables
        '.exe', '.dmg', '.iso', '.msi', '.deb', '.rpm',
    )
    return not any(path.endswith(ext) for ext in bad_exts)


# ── robots.txt ─────────────────────────────────────────────────────


def _build_robots(start_url: str) -> urllib.robotparser.RobotFileParser | None:
    p = urllib.parse.urlparse(start_url)
    robots_url = f'{p.scheme}://{p.netloc}/robots.txt'
    rp = urllib.robotparser.RobotFileParser()
    try:
        with httpx.Client(timeout=10, headers={'User-Agent': USER_AGENT}) as client:
            r = client.get(robots_url, follow_redirects=True)
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
            return rp
        # 404 robots.txt = effectively allow-all (RFC 9309)
        return None
    except Exception:
        return None


# ── Content extraction ────────────────────────────────────────────


def _extract(url: str, html: str) -> tuple[str, str]:
    """Returns (title, markdown). Empty markdown = nothing useful
    extracted; caller should skip writing the file."""
    import trafilatura

    extracted = trafilatura.extract(
        html,
        url=url,
        output_format='markdown',
        include_comments=False,
        include_tables=True,
        include_links=False,        # keep prose clean for embedding
        favor_precision=True,        # prefer dropping ambiguous content
        with_metadata=False,
    ) or ''

    # Title — try metadata first, fall back to extracting <title>
    title = ''
    meta = trafilatura.extract_metadata(html)
    if meta and meta.title:
        title = meta.title.strip()
    if not title:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()

    return title, extracted.strip()


def _extract_links(html: str) -> list[str]:
    """Find all href values in the HTML. Trafilatura strips links
    out of the extracted body so we mine them from the raw HTML."""
    return re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)


# ── Crawler ────────────────────────────────────────────────────────


def crawl(args) -> dict:
    start_url = _normalise_url(args.start_url)
    host = urllib.parse.urlparse(start_url).netloc

    include_re = re.compile(args.include) if args.include else None
    exclude_re = re.compile(args.exclude) if args.exclude else None

    rp = None if args.ignore_robots else _build_robots(start_url)
    if rp:
        logger.info('robots.txt loaded for %s', host)
    elif args.ignore_robots:
        logger.warning('robots.txt ignored per --ignore-robots')

    out_dir = Path(args.output_dir).expanduser().resolve() / args.machine
    out_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    queue: deque[str] = deque([start_url])
    if args.sitemap_url:
        try:
            with httpx.Client(timeout=15, headers={'User-Agent': USER_AGENT}) as client:
                sm = client.get(args.sitemap_url, follow_redirects=True)
            if sm.status_code == 200:
                sitemap_urls = re.findall(r'<loc>([^<]+)</loc>', sm.text)
                logger.info('sitemap loaded: %d URLs from %s', len(sitemap_urls), args.sitemap_url)
                for u in sitemap_urls:
                    queue.append(_normalise_url(u))
            else:
                logger.warning('sitemap fetch returned %d — continuing with crawl-only', sm.status_code)
        except Exception as exc:
            logger.warning('sitemap fetch failed: %s — continuing with crawl-only', exc)
    n_fetched = 0
    n_written = 0
    n_skipped_robots = 0
    n_skipped_other = 0
    last_request = 0.0
    delay = 1.0 / max(args.rps, 0.1)

    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,*/*',
        'Accept-Language': 'en',
    }

    with httpx.Client(
        timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
        headers=headers,
        follow_redirects=True,
    ) as client:
        while queue and n_fetched < args.max_pages:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)

            if not _same_host(url, host):
                n_skipped_other += 1
                continue
            if not _looks_like_html(url):
                n_skipped_other += 1
                continue
            if include_re and not include_re.search(url):
                n_skipped_other += 1
                continue
            if exclude_re and exclude_re.search(url):
                n_skipped_other += 1
                continue
            if rp and not rp.can_fetch(USER_AGENT, url):
                n_skipped_robots += 1
                logger.info('  ROBOTS  %s', url)
                continue

            # Politeness: 1/rps seconds between requests
            elapsed = time.monotonic() - last_request
            if elapsed < delay:
                time.sleep(delay - elapsed)
            last_request = time.monotonic()

            try:
                r = client.get(url)
            except Exception as exc:
                logger.warning('  ERR     %s — %s: %s', url, type(exc).__name__, exc)
                n_skipped_other += 1
                continue

            n_fetched += 1
            if r.status_code != 200:
                logger.info('  %d     %s', r.status_code, url)
                continue
            ct = (r.headers.get('content-type') or '').lower()
            if 'html' not in ct:
                logger.debug('  not html  %s  (%s)', url, ct)
                continue

            html = r.text
            try:
                title, body = _extract(url, html)
            except Exception as exc:
                logger.warning('  EXTRACT %s — %s', url, exc)
                continue

            if body and not args.dry_run:
                slug = _slug_for_url(url, host)
                target = out_dir / f'{slug}.md'
                # Frontmatter + content. The upload pipeline reads the
                # plain text content; the frontmatter here is purely
                # for human-readability of the staged file.
                fm = (
                    f'---\n'
                    f'machine: {args.machine}\n'
                    f'source_url: {url}\n'
                    f'scraped: {time.strftime("%Y-%m-%d")}\n'
                    f'title: {title or "(untitled)"}\n'
                    f'---\n\n'
                )
                heading = f'# {title}\n\n' if title else ''
                target.write_text(fm + heading + body + '\n', encoding='utf-8')
                n_written += 1

            logger.info(
                '  %s  %s  (title=%r, %d chars)',
                'DRY ' if args.dry_run else 'OK  ',
                url,
                (title[:60] + '...') if len(title) > 60 else title,
                len(body),
            )

            # Discover links (we want links from EVERY page, not just
            # those we successfully extract content from — a low-content
            # index page may still link to high-content articles).
            # Apply the same filters at enqueue time so we don't waste
            # cycles dequeuing junk later.
            for href in _extract_links(html):
                next_url = _normalise_url(href, base=url)
                if next_url in seen:
                    continue
                if not _same_host(next_url, host):
                    continue
                if not _looks_like_html(next_url):
                    continue
                queue.append(next_url)

    return {
        'fetched': n_fetched,
        'written': n_written,
        'skipped_robots': n_skipped_robots,
        'skipped_other': n_skipped_other,
        'queue_remaining': len(queue),
        'output_dir': str(out_dir),
    }


# ── Main ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    p = argparse.ArgumentParser(
        prog='python scripts/scrape_docs.py',
        description=__doc__.strip().split('\n')[0],
    )
    p.add_argument('--start-url', required=True,
                   help='URL to start crawling from. Same-host scope only. '
                        'Also used to anchor the host scope when --sitemap-url '
                        'is supplied.')
    p.add_argument('--sitemap-url', default=None,
                   help='Optional sitemap.xml URL — when supplied, the queue is '
                        'seeded from <loc> entries in the sitemap rather than '
                        'discovered by link-crawling. Cleaner for sites with '
                        'JS-rendered nav (MkDocs Material, etc.) where href= '
                        'links point to wrong paths in the static HTML.')
    p.add_argument('--machine', required=True,
                   help='Canonical machine nickname (matches frontmatter machine: in identity card).')
    p.add_argument('--output-dir', default='.tmp/scrape-staging',
                   help='Staging dir for scraped markdown files.')
    p.add_argument('--include', default=None,
                   help='Regex; only crawl URLs matching this.')
    p.add_argument('--exclude', default=None,
                   help='Regex; skip URLs matching this.')
    p.add_argument('--max-pages', type=int, default=1000,
                   help='Hard cap on pages fetched (default 1000).')
    p.add_argument('--rps', type=float, default=1.0,
                   help='Requests per second (default 1.0).')
    p.add_argument('--ignore-robots', action='store_true',
                   help='Ignore robots.txt. Use only with explicit permission.')
    p.add_argument('--dry-run', action='store_true',
                   help='Walk + extract but do not write files to disk.')
    args = p.parse_args(argv)

    out = crawl(args)

    print()
    print('----- scrape summary -----')
    print(f'  start:           {args.start_url}')
    print(f'  machine:         {args.machine}')
    print(f'  fetched:         {out["fetched"]}')
    print(f'  written:         {out["written"]}')
    print(f'  skipped (robots): {out["skipped_robots"]}')
    print(f'  skipped (other): {out["skipped_other"]}')
    print(f'  queue remaining: {out["queue_remaining"]}')
    print(f'  output dir:      {out["output_dir"]}')
    print()
    if not args.dry_run and out['written'] > 0:
        print('Next: python scripts/upload_manuals_from_drive.py'
              f' --folder {args.output_dir} --commit')
    return 0


if __name__ == '__main__':
    sys.exit(main())
