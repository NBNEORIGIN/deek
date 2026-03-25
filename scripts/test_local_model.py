"""
scripts/test_local_model.py — Local model VRAM fit test

Tests whether deepseek-coder-v2:16b runs acceptably on your GPU.
Also benchmarks qwen2.5-coder:7b for comparison if available.

Usage:
    python scripts/test_local_model.py [--model MODEL] [--pull]

Options:
    --model  MODEL    Override the model to test (default: deepseek-coder-v2:16b)
    --pull            Auto-pull the model if not present (takes a while on first run)
    --skip-pull       Skip pull attempt even if model is missing

Results to look for:
    ✓ Loaded:        model responded without OOM
    ✓ Speed:         > 8 t/s = usable, > 15 t/s = comfortable
    ✓ Quality:       correct Python with docstring and type hints
    ✓ VRAM headroom: Windows still stable after inference
"""
import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path so we can import claw modules
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import httpx

OLLAMA_BASE = 'http://localhost:11434'

BENCHMARK_PROMPT = (
    'Write a Python function that returns the Fibonacci sequence up to n. '
    'Include a docstring and type hints. Make it efficient.'
)

MODELS_TO_TEST = [
    ('deepseek-coder-v2:16b', 10.5, 'Primary candidate'),
    ('qwen2.5-coder:7b',       5.5, 'Fallback (always fits 8 GB GPU)'),
]

SEPARATOR = '─' * 60


def banner(text: str):
    print(f'\n{SEPARATOR}')
    print(f'  {text}')
    print(SEPARATOR)


def gpu_info() -> dict:
    """Get VRAM usage via nvidia-smi. Returns empty dict if unavailable."""
    try:
        out = subprocess.check_output(
            ['nvidia-smi',
             '--query-gpu=name,memory.total,memory.used,memory.free',
             '--format=csv,noheader,nounits'],
            text=True, timeout=5,
        ).strip()
        name, total, used, free = [x.strip() for x in out.split(',')]
        return {
            'gpu': name,
            'total_mb': int(total),
            'used_mb': int(used),
            'free_mb': int(free),
        }
    except Exception:
        return {}


def print_gpu(label: str):
    info = gpu_info()
    if info:
        used_gb = info['used_mb'] / 1024
        free_gb = info['free_mb'] / 1024
        total_gb = info['total_mb'] / 1024
        print(f'  GPU [{label}]: {info["gpu"]} | '
              f'Used {used_gb:.1f} GB / {total_gb:.1f} GB '
              f'({free_gb:.1f} GB free)')
    else:
        print(f'  GPU [{label}]: nvidia-smi not available — skipping VRAM report')


async def ollama_tags() -> list[str]:
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f'{OLLAMA_BASE}/api/tags')
        return [m['name'] for m in r.json().get('models', [])]


async def pull_model(model: str):
    print(f'  Pulling {model} — this may take several minutes on first run...')
    proc = await asyncio.create_subprocess_exec(
        'ollama', 'pull', model,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async for line in proc.stdout:
        text = line.decode().rstrip()
        if text:
            print(f'    {text}')
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f'ollama pull {model} failed (exit {proc.returncode})')
    print(f'  ✓ {model} pulled successfully')


async def test_model(model: str, do_pull: bool) -> dict:
    """
    Run a single model through the benchmark.
    Returns a result dict with speed, quality, and fit verdict.
    """
    result = {
        'model': model,
        'status': 'untested',
        'tokens_per_sec': 0.0,
        'response': '',
        'fits': None,
        'error': '',
    }

    # Check if pulled
    try:
        tags = await ollama_tags()
    except Exception as exc:
        result['status'] = 'error'
        result['error'] = f'Ollama unreachable: {exc}'
        return result

    is_pulled = any(model in t for t in tags)

    if not is_pulled:
        if do_pull:
            try:
                await pull_model(model)
            except Exception as exc:
                result['status'] = 'pull_failed'
                result['error'] = str(exc)
                return result
        else:
            result['status'] = 'not_pulled'
            result['error'] = f'{model} is not pulled. Run with --pull to download.'
            return result

    # Pre-inference VRAM snapshot
    print_gpu('before')

    # Run inference
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': 'You are a helpful Python coding assistant.'},
            {'role': 'user',   'content': BENCHMARK_PROMPT},
        ],
        'stream': False,
        'options': {'temperature': 0.1, 'num_ctx': 4096},
    }

    print(f'  Running inference (may take 30-90s on cold start)...')
    t_start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f'{OLLAMA_BASE}/api/chat', json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ReadTimeout:
        result['status'] = 'error'
        result['fits'] = None  # unknown — may fit but just very slow
        result['error'] = 'Timed out after 300s — model too slow on this hardware'
        return result
    except Exception as exc:
        err = str(exc)
        result['status'] = 'error'
        result['fits'] = False
        if 'out of memory' in err.lower() or 'oom' in err.lower():
            result['error'] = f'OOM — {model} does not fit in VRAM'
        else:
            result['error'] = f'Inference error: {err}'
        return result

    elapsed = time.perf_counter() - t_start

    # Post-inference VRAM snapshot
    print_gpu('after')

    eval_count = data.get('eval_count', 0)
    eval_duration_ns = data.get('eval_duration', 1)
    tps = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns else 0

    result['status'] = 'ok'
    result['fits'] = True
    result['tokens_per_sec'] = round(tps, 1)
    result['response'] = data.get('message', {}).get('content', '')
    result['elapsed_sec'] = round(elapsed, 1)
    result['eval_tokens'] = eval_count

    return result


def print_result(r: dict, vram_gb: float, description: str):
    model = r['model']
    banner(f'{model}  ({description} — ~{vram_gb:.1f} GB VRAM)')

    if r['status'] == 'not_pulled':
        print(f'  ⚠  Not pulled. Run with --pull to download and test.')
        return

    if r['status'] == 'pull_failed':
        print(f'  ✗  Pull failed: {r["error"]}')
        return

    if r['status'] == 'error':
        print(f'  ✗  {r["error"]}')
        if r.get('fits') is False:
            print(f'\n  VERDICT: {model} does NOT fit on this GPU')
            if model != 'qwen2.5-coder:7b':
                print(f'           Fall back to qwen2.5-coder:7b')
        elif r.get('fits') is None:
            print(f'\n  VERDICT: {model} timed out — hardware may be too slow for this model')
        return

    tps = r['tokens_per_sec']
    speed_label = (
        '🟢 comfortable (15+ t/s)' if tps >= 15 else
        '🟡 usable (8–15 t/s)'     if tps >= 8  else
        '🔴 slow (< 8 t/s)'
    )

    print(f'  ✓  Loaded without OOM')
    print(f'  ✓  Speed:    {tps:.1f} t/s  {speed_label}')
    print(f'  ✓  Elapsed:  {r.get("elapsed_sec", "?")} s total')
    print(f'  ✓  Tokens:   {r.get("eval_tokens", "?")} output')
    print()
    print('  Response:')
    for line in r['response'].splitlines():
        print(f'    {line}')

    print()
    if tps >= 8:
        print(f'  VERDICT: {model} fits and runs at {tps:.1f} t/s ✓')
        if vram_gb > 8:
            print(f'           Note: uses ~{vram_gb:.1f} GB — ensure no other GPU apps running')
    else:
        print(f'  VERDICT: {model} fits but is slow ({tps:.1f} t/s) — consider the API tier')


async def main():
    parser = argparse.ArgumentParser(description='Test local Ollama model fit')
    parser.add_argument('--model', default=None, help='Override model to test')
    parser.add_argument('--pull', action='store_true', help='Auto-pull if not present')
    parser.add_argument('--skip-pull', action='store_true', help='Skip pull attempt')
    args = parser.parse_args()

    banner('CLAW — Local Model VRAM Fit Test')
    print(f'  Ollama: {OLLAMA_BASE}')
    print_gpu('baseline')

    # Check Ollama is running
    try:
        tags = await ollama_tags()
        print(f'\n  Ollama running. Pulled models: {tags or ["(none)"]}')
    except Exception:
        print('\n  ✗  Ollama is not running. Start it with: ollama serve')
        sys.exit(1)

    do_pull = args.pull and not args.skip_pull

    # Which models to test
    if args.model:
        models_to_run = [(args.model, VRAM_REQUIREMENTS.get(args.model, 4.0), 'Custom')]
    else:
        models_to_run = MODELS_TO_TEST

    results = []
    for model, vram_gb, description in models_to_run:
        print(f'\nTesting {model}...')
        r = await test_model(model, do_pull=do_pull)
        results.append((r, vram_gb, description))

    # Print all results
    for r, vram_gb, description in results:
        print_result(r, vram_gb, description)

    # Summary recommendation
    banner('Summary & Recommendation')
    primary = results[0][0]
    if primary['fits']:
        tps = primary['tokens_per_sec']
        print(f'  ✓ {primary["model"]} fits — set in .env:')
        print(f'    OLLAMA_MODEL_PREFERRED={primary["model"]}')
        if tps < 8:
            print(f'  ⚠  Speed is low ({tps:.1f} t/s) — DeepSeek API (Tier 2) may be faster')
    elif len(results) > 1 and results[1][0]['fits']:
        fallback_model = results[1][0]['model']
        print(f'  ✗ Primary model does not fit')
        print(f'  ✓ {fallback_model} fits — set in .env:')
        print(f'    OLLAMA_MODEL_PREFERRED={fallback_model}')
        print(f'    OLLAMA_MODEL={fallback_model}')
    else:
        print(f'  ✗ No local model available — all inference via API tier')
        print(f'    DeepSeek API (Tier 2) will handle most tasks cheaply')

    print()
    print('  Pull commands:')
    print('    ollama pull deepseek-coder-v2:16b')
    print('    ollama pull qwen2.5-coder:7b')
    print('    ollama pull nomic-embed-text')


# Expose VRAM_REQUIREMENTS for import
from core.models.ollama_client import VRAM_REQUIREMENTS

if __name__ == '__main__':
    asyncio.run(main())
