"""
Web access tools for CLAW agent.
All operations are read-only — SAFE, auto-approved.

Use cases:
  - Check phloe.co.uk is responding after a deployment
  - Fetch API or library documentation
  - Search for error solutions
  - Verify tenant sites: demnurse.nbne.uk, etc.
"""
import re
import warnings
import httpx
from .registry import Tool, RiskLevel

# Suppress the InsecureRequestWarning that appears when verify=False
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

_HEADERS = {'User-Agent': 'CLAW/1.0 (NBNE Development Agent)'}


def _web_fetch(project_root: str, url: str = '') -> str:
    """
    Fetch a URL and return readable content.
    Strips HTML tags for prose pages; returns raw for JSON.
    """
    if not url:
        return "Error: url parameter required"
    if not url.startswith(('http://', 'https://')):
        return f"Error: only http/https URLs allowed, got: {url}"

    try:
        with httpx.Client(
            timeout=15,
            follow_redirects=True,
            verify=False,  # skip SSL verification — handles self-signed/mismatched certs
        ) as client:
            response = client.get(url, headers=_HEADERS)

        ct = response.headers.get('content-type', '')
        status = response.status_code

        if 'html' in ct:
            text = response.text
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 3000:
                text = text[:3000] + '… [truncated]'
            return f"URL: {url}\nStatus: {status}\n\n{text}"

        if 'json' in ct:
            return f"URL: {url}\nStatus: {status}\n\n{response.text[:3000]}"

        return (
            f"URL: {url}\nStatus: {status}\n"
            f"Content-Type: {ct}\n[Non-text content]"
        )

    except httpx.TimeoutException:
        return f"TIMEOUT: {url} did not respond within 15 seconds"
    except httpx.ConnectError as e:
        return f"CONNECTION ERROR: could not reach {url} — {e}"
    except Exception as e:
        return f"ERROR fetching {url}: {type(e).__name__}: {e}"


def _web_check_status(project_root: str, url: str = '') -> str:
    """
    Check a URL — tries HEAD first, falls back to GET if blocked.
    Cloudflare and many CDNs reject HEAD requests but allow GET.
    """
    if not url:
        return "Error: url parameter required"

    import time
    try:
        start = time.time()
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            try:
                response = client.head(url, headers=_HEADERS)
                # Some servers return 405 Method Not Allowed for HEAD
                if response.status_code == 405:
                    raise httpx.HTTPStatusError("HEAD not allowed", request=response.request, response=response)
            except (httpx.HTTPStatusError, httpx.ConnectError):
                # Fall back to GET — works with Cloudflare and most CDNs
                response = client.get(url, headers=_HEADERS)
        elapsed = (time.time() - start) * 1000
        return (
            f"URL: {url}\n"
            f"Status: {response.status_code}\n"
            f"Response time: {elapsed:.0f}ms\n"
            f"Final URL: {response.url}"
        )
    except Exception as e:
        return f"Error checking {url}: {e}"


def _web_search(project_root: str, query: str = '') -> str:
    """
    Search the web using DuckDuckGo instant answers (no API key needed).
    Good for finding documentation, error explanations, or library info.
    """
    if not query:
        return "Error: query parameter required"

    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(
                'https://api.duckduckgo.com/',
                params={
                    'q': query,
                    'format': 'json',
                    'no_html': '1',
                    'skip_disambig': '1',
                },
            )
        data = response.json()
        results = []

        if data.get('Abstract'):
            results.append(f"Summary: {data['Abstract']}")
            if data.get('AbstractURL'):
                results.append(f"Source: {data['AbstractURL']}")

        if data.get('Answer'):
            results.append(f"Answer: {data['Answer']}")

        for topic in (data.get('RelatedTopics') or [])[:3]:
            if isinstance(topic, dict) and topic.get('Text'):
                results.append(f"  - {topic['Text'][:150]}")

        if not results:
            return (
                f"No instant results for: {query}\n"
                f"Try web_fetch with a specific documentation URL instead."
            )
        return '\n'.join(results)

    except Exception as e:
        return f"Search error: {e}"


# ── Tool definitions ─────────────────────────────────────────────────────────

web_fetch_tool = Tool(
    name='web_fetch',
    description=(
        'Fetch a URL and return its text content. '
        'Use to check live sites (phloe.co.uk, demnurse.nbne.uk), '
        'fetch API documentation, or read any public web page. '
        'Pass url parameter (http/https only).'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_web_fetch,
    required_permission='web_fetch',
)

web_check_status_tool = Tool(
    name='web_check_status',
    description=(
        'Quick health check of a URL — returns HTTP status code and '
        'response time in ms. Use after deployments to verify a site is up. '
        'Pass url parameter.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_web_check_status,
    required_permission='web_check_status',
)

web_search_tool = Tool(
    name='web_search',
    description=(
        'Search the web for documentation, error messages, or solutions. '
        'Uses DuckDuckGo instant answers — no API key required. '
        'Pass query parameter.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_web_search,
    required_permission='web_search',
)
