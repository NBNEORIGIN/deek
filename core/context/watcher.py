"""
File Watcher — incremental pgvector reindex on file save.

Uses watchdog to monitor the project codebase. When a file is saved,
only that file is reindexed (not the whole project).

Threading model:
    watchdog runs event handlers in its own observer thread.
    The indexer is async (uses httpx for Ollama embedding calls).
    We bridge using asyncio.run_coroutine_threadsafe() — the main event
    loop reference is passed in at construction time.

Debounce:
    Rapid saves on the same file (e.g. auto-save every 300ms) trigger
    only ONE reindex, scheduled 2 seconds after the last save event.
"""
import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Patterns excluded from watching
_EXCLUDE_DIRS = frozenset({
    '__pycache__', '.git', 'node_modules', '.venv', 'venv',
    'dist', 'build', '.next', 'migrations', '.pytest_cache',
    'coverage', '.codeium', '.windsurf',
})
_EXCLUDE_SUFFIXES = frozenset({'.pyc', '.pyo', '.pyd', '.log', '.env'})
_EXCLUDE_NAMES = frozenset({'.env', '.env.local', '.env.production'})


def _should_exclude(path: str) -> bool:
    p = Path(path)
    for part in p.parts:
        if part in _EXCLUDE_DIRS:
            return True
    if p.suffix in _EXCLUDE_SUFFIXES:
        return True
    if p.name in _EXCLUDE_NAMES:
        return True
    return False


class FileWatcher:
    """
    Watches a directory and reindexes changed files via CodeIndexer.

    Usage:
        watcher = FileWatcher(
            path='/path/to/codebase',
            indexer=code_indexer_instance,
            loop=asyncio.get_event_loop(),
        )
        watcher.start()
        # ... later ...
        watcher.stop()
    """

    DEBOUNCE_SECONDS = 2.0

    def __init__(
        self,
        path: str,
        indexer,                              # CodeIndexer instance
        loop: asyncio.AbstractEventLoop,
    ):
        self.path = path
        self.indexer = indexer
        self.loop = loop
        self._observer = None
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self.last_reindex_at: datetime | None = None
        self._active = False

    def start(self):
        """Start monitoring. Safe to call multiple times."""
        if self._active:
            return
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class _Handler(FileSystemEventHandler):
                def __init__(self_, watcher):
                    self_._watcher = watcher

                def on_modified(self_, event):
                    if not event.is_directory:
                        self_._watcher._schedule(event.src_path)

                def on_created(self_, event):
                    if not event.is_directory:
                        self_._watcher._schedule(event.src_path)

            self._observer = Observer()
            self._observer.schedule(_Handler(self), self.path, recursive=True)
            self._observer.start()
            self._active = True
            logger.info(f'[watcher] monitoring {self.path}')
        except ImportError:
            logger.warning(
                '[watcher] watchdog not installed — file watching disabled. '
                'Run: pip install watchdog'
            )
        except Exception as exc:
            logger.error(f'[watcher] failed to start: {exc}')

    def stop(self):
        """Stop monitoring and cancel pending debounce timers."""
        self._active = False
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)

    def _schedule(self, file_path: str):
        """Schedule a reindex for file_path, cancelling any pending timer."""
        if _should_exclude(file_path):
            return
        with self._lock:
            existing = self._timers.pop(file_path, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(
                self.DEBOUNCE_SECONDS,
                self._fire,
                args=(file_path,),
            )
            self._timers[file_path] = timer
            timer.start()

    def _fire(self, file_path: str):
        """Called by the debounce timer — bridges into the async event loop."""
        with self._lock:
            self._timers.pop(file_path, None)

        if not self.loop.is_running():
            return

        async def _reindex():
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.indexer.index_file(file_path),
                )
                if result.get('status') == 'indexed':
                    self.last_reindex_at = datetime.now(timezone.utc)
                    logger.info(
                        f'[watcher] reindexed {file_path} '
                        f'({result["chunks"]} chunks)'
                    )
                elif result.get('status') == 'error':
                    logger.warning(
                        f'[watcher] reindex error for {file_path}: '
                        f'{result.get("error")}'
                    )
            except Exception as exc:
                logger.error(f'[watcher] _reindex raised: {exc}')

        asyncio.run_coroutine_threadsafe(_reindex(), self.loop)
