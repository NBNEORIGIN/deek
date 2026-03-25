"""
CLAW system tray application.

Owns the full process lifecycle — spawns, monitors and auto-restarts
the API and web servers directly as subprocesses.

No Windows services or admin rights required after the one-time
`remove_services.ps1` migration.
"""
import os
import threading
import time
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

import httpx
import pystray
from PIL import Image, ImageDraw

# ── Config ────────────────────────────────────────────────────────────────────

CLAW_DIR      = Path(__file__).parent.parent
API_URL       = "http://localhost:8765"
WEB_URL       = "http://localhost:3000"
API_KEY       = "claw-dev-key-change-in-production"
CHECK_SECS    = 10
RESTART_DELAY = 5      # seconds before restarting a crashed process
NO_WINDOW     = 0x08000000   # CREATE_NO_WINDOW — no console popup

# ── .env loader ───────────────────────────────────────────────────────────────

def _load_dotenv(path: Path) -> dict:
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


_DOTENV = _load_dotenv(CLAW_DIR / ".env")


def _make_env(**extra) -> dict:
    """Inherit current environment, overlay .env values, then kwargs."""
    env = os.environ.copy()
    env.update(_DOTENV)
    env.update(extra)
    return env


# ── Locate binaries ───────────────────────────────────────────────────────────

def _find_node() -> str:
    try:
        out = subprocess.check_output(
            ["where", "node"], text=True, creationflags=NO_WINDOW
        )
        return out.strip().splitlines()[0]
    except Exception:
        return "node"


_PYTHON  = str(CLAW_DIR / ".venv" / "Scripts" / "python.exe")
_NODE    = _find_node()
_NEXT_JS = str(CLAW_DIR / "web" / "node_modules" / "next" / "dist" / "bin" / "next")

# ── ManagedProcess ────────────────────────────────────────────────────────────

class ManagedProcess:
    """Wraps a single subprocess: start / stop / restart, logs to file."""

    def __init__(self, name: str, label: str, cmd: list,
                 cwd: str, env: dict, log: Path):
        self.name     = name
        self.label    = label
        self.stopping = False
        self._cmd     = cmd
        self._cwd     = cwd
        self._env     = env
        self._log     = log
        self._proc:  Optional[subprocess.Popen] = None
        self._log_f  = None
        self._lock   = threading.Lock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start(self):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            self._log.parent.mkdir(parents=True, exist_ok=True)
            if self._log_f:
                try:
                    self._log_f.close()
                except Exception:
                    pass
            self._log_f = open(self._log, "a", encoding="utf-8", errors="replace")
            self._proc = subprocess.Popen(
                self._cmd,
                cwd=self._cwd,
                env=self._env,
                stdout=self._log_f,
                stderr=self._log_f,
                creationflags=NO_WINDOW,
            )

    def stop(self):
        self.stopping = True
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            if self._log_f:
                try:
                    self._log_f.close()
                except Exception:
                    pass
                self._log_f = None
        self.stopping = False

    def restart(self):
        self.stop()
        time.sleep(1)
        self.start()


# ── Supervisor ────────────────────────────────────────────────────────────────

class Supervisor:
    """Manages a collection of ManagedProcess instances with auto-restart."""

    def __init__(self):
        self._procs:      dict[str, ManagedProcess] = {}
        self._active      = False
        self._restart_at: dict[str, float] = {}

    def add(self, **kwargs):
        p = ManagedProcess(**kwargs)
        self._procs[p.name] = p

    def start_all(self):
        self._active = True
        for p in self._procs.values():
            p.start()
        threading.Thread(target=self._watch, daemon=True).start()

    def stop_all(self):
        self._active = False
        for p in self._procs.values():
            p.stop()

    def restart(self, name: str):
        if name in self._procs:
            threading.Thread(
                target=self._procs[name].restart, daemon=True
            ).start()

    def restart_all(self):
        threading.Thread(
            target=lambda: [p.restart() for p in self._procs.values()],
            daemon=True,
        ).start()

    def _watch(self):
        """Check every 2 s; schedule restarts after RESTART_DELAY."""
        while self._active:
            now = time.time()
            for name, p in self._procs.items():
                if p.stopping:
                    self._restart_at.pop(name, None)
                    continue
                if not p.running:
                    if name not in self._restart_at:
                        self._restart_at[name] = now + RESTART_DELAY
                    elif now >= self._restart_at[name]:
                        p.start()
                        self._restart_at.pop(name, None)
                else:
                    self._restart_at.pop(name, None)
            time.sleep(2)


# ── Icon drawing ──────────────────────────────────────────────────────────────

def make_icon(status: str) -> Image.Image:
    colours = {
        "ok":       ("#1a1a2e", "#00d4aa"),
        "warn":     ("#1a1a2e", "#f59e0b"),
        "error":    ("#1a1a2e", "#ef4444"),
        "starting": ("#1a1a2e", "#6b7280"),
    }
    bg, dot = colours.get(status, colours["starting"])
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, 62, 62], radius=14,
                            fill=bg, outline="#333355", width=2)
    draw.text((14, 12), "C", fill="#e2e8f0", font=None)
    draw.text((28, 12), "L", fill="#e2e8f0", font=None)
    draw.ellipse([42, 42, 58, 58], fill=dot)
    return img


# ── Health check ──────────────────────────────────────────────────────────────

def check_health() -> dict:
    try:
        r = httpx.get(
            f"{API_URL}/health",
            headers={"X-API-Key": API_KEY},
            timeout=4,
        )
        return r.json()
    except Exception:
        return {}


# ── Tray ──────────────────────────────────────────────────────────────────────

class ClawTray:

    def __init__(self, supervisor: Supervisor):
        self._sv     = supervisor
        self._status = "starting"
        self._icon   = pystray.Icon(
            name="CLAW",
            icon=make_icon("starting"),
            title="CLAW — starting…",
            menu=self._build_menu(),
        )

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open Web Chat",
                             lambda: webbrowser.open(WEB_URL)),
            pystray.MenuItem("Open Cursor",
                             lambda: subprocess.Popen(
                                 ["cursor", str(CLAW_DIR)],
                                 creationflags=NO_WINDOW,
                             )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart API",  lambda: self._sv.restart("api")),
            pystray.MenuItem("Restart Web",  lambda: self._sv.restart("web")),
            pystray.MenuItem("Restart All",  lambda: self._sv.restart_all()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop All",     self._stop_all),
            pystray.MenuItem("Quit Tray",    self._quit),
        )

    def _stop_all(self, *_):
        threading.Thread(target=self._sv.stop_all, daemon=True).start()

    def _quit(self, *_):
        self._sv.stop_all()
        self._icon.stop()

    def _poll(self):
        while True:
            health = check_health()
            if not health:
                status = "error"
                tip    = "CLAW — API offline"
            elif not health.get("ollama_available"):
                status = "warn"
                tip    = "CLAW — Ollama offline (API running)"
            else:
                status = "ok"
                model  = health.get("ollama_model", "?")
                tip    = f"CLAW — ready  •  {model}"

            if status != self._status:
                self._status    = status
                self._icon.icon = make_icon(status)
            self._icon.title = tip
            time.sleep(CHECK_SECS)

    def run(self):
        threading.Thread(target=self._poll, daemon=True).start()
        self._icon.run()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sv = Supervisor()

    sv.add(
        name="api",
        label="API",
        cmd=[
            _PYTHON, "-m", "uvicorn", "api.main:app",
            "--host", "0.0.0.0", "--port", "8765", "--workers", "1",
        ],
        cwd=str(CLAW_DIR),
        env=_make_env(
            PYTHONIOENCODING="utf-8",
            PYTHONUTF8="1",
            PYTHONUNBUFFERED="1",
        ),
        log=CLAW_DIR / "logs" / "api" / "stdout.log",
    )

    sv.add(
        name="web",
        label="Web",
        cmd=[_NODE, _NEXT_JS, "dev", "-p", "3000"],
        cwd=str(CLAW_DIR / "web"),
        env=_make_env(PORT="3000"),
        log=CLAW_DIR / "logs" / "web" / "stdout.log",
    )

    sv.start_all()
    ClawTray(sv).run()
