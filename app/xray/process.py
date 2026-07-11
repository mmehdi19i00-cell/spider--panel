"""Xray process lifecycle manager — robust, observable, Railway-aware.

Design goals (from the spec):
  * Never hide stderr. We capture stdout + stderr in full and persist them to
    a ring buffer (last N lines) AND to a file under the data dir, so the
    dashboard's "View Xray Logs" page shows the real parser error instead of
    only "returncode 23".
  * Validate before starting: `xray run -test -config ...` must pass, else we
    return the full parser error and refuse to start.
  * Correct start/stop/restart/reload. No zombies (we reap via pidfile).
  * Auto-restart: a watchdog restarts Xray if it crashes unexpectedly, with
    backoff. This keeps the proxy alive through transient faults.
  * Railway-aware: the inbound binds an INTERNAL port (never 443 inside
    Railway's container). The client-facing port comes from Railway's TCP
    proxy env, handled by the config/subscription builders.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from collections import deque
from pathlib import Path

from app.core.config import settings
from app.core.logging import log, log_xray


class XrayProcessManager:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._pidfile = Path(settings.data_dir) / "xray" / "xray.pid"
        self._logdir = Path(settings.data_dir) / "xray"
        self._logfile = self._logdir / "xray.log"
        self._last = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "command": "",
            "cwd": "",
            "error": None,
            "test_ok": None,
            "test_message": "",
        }
        self._ring: deque[str] = deque(maxlen=500)
        self._watchdog_task: asyncio.Task | None = None
        self._stopping = False

    # -- helpers ----------------------------------------------------------
    def _read_pid(self) -> int | None:
        try:
            pid = int(self._pidfile.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    def _write_pid(self, pid: int) -> None:
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        self._pidfile.write_text(str(pid))

    def _clear_pid(self) -> None:
        try:
            self._pidfile.unlink()
        except FileNotFoundError:
            pass

    def _append_log(self, line: str) -> None:
        self._ring.append(line)
        try:
            with open(self._logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # -- validation -------------------------------------------------------
    def validate_config_file(self, path: str | None = None) -> dict:
        """Run `xray run -test -config ...`. Returns a structured result.

        On failure we print the FULL xray error (stdout+stderr) so the cause of
        a broken config is never hidden. If validation fails, the caller must NOT
        start Xray.
        """
        path = path or settings.xray_config_path
        binary = settings.XRAY_BINARY_PATH
        result = {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "command": "",
            "cwd": os.getcwd(),
            "config_path": path,
            "error": None,
            "message": "",
        }
        if not Path(binary).exists():
            result["error"] = f"xray binary not found at {binary}"
            result["message"] = result["error"]
            log_xray("validate.fail", **{k: v for k, v in result.items() if k not in ("ok",)})
            return result
        if not Path(path).exists():
            result["error"] = f"config not found at {path}"
            result["message"] = result["error"]
            return result
        cmd = [binary, "run", "-test", "-config", path]
        result["command"] = " ".join(cmd)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20,
                cwd=str(self._logdir),
            )
            result["returncode"] = proc.returncode
            result["stdout"] = (proc.stdout or "").strip()
            result["stderr"] = (proc.stderr or "").strip()
            if proc.returncode == 0:
                result["ok"] = True
                result["message"] = result["stdout"] or "config valid"
                log_xray("validate.ok", config=path)
            else:
                # Print the FULL error so the operator sees the real cause.
                detail = result["stderr"] or result["stdout"] or "validation failed"
                result["message"] = detail
                log.error("=" * 60)
                log.error("XRAY CONFIG VALIDATION FAILED — not starting Xray")
                log.error(f"command: {result['command']}")
                log.error(f"--- xray stderr ---\n{detail}")
                if result["stdout"]:
                    log.error(f"--- xray stdout ---\n{result['stdout']}")
                log.error("=" * 60)
        except subprocess.TimeoutExpired as e:
            result["error"] = "validation timed out"
            result["stderr"] = (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else str(e.stderr or "")
            result["stdout"] = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else str(e.stdout or "")
            result["message"] = result["error"]
        except OSError as e:
            result["error"] = str(e)
            result["message"] = str(e)
        # persist for UI
        self._last["test_ok"] = result["ok"]
        self._last["test_message"] = result["message"]
        self._last["stdout"] = result["stdout"]
        self._last["stderr"] = result["stderr"]
        self._last["returncode"] = result["returncode"]
        self._last["command"] = result["command"]
        self._last["cwd"] = result["cwd"]
        return result

    def print_startup_banner(
        self,
        active_domain: str | None = None,
        reality_enabled: bool | None = None,
    ) -> None:
        """Log the resolved ports / domain / reality state for quick diagnosis.

        `active_domain` and `reality_enabled` are optional pre-computed values
        (callers in an async context pass them). When omitted we read what we can
        from the on-disk config without spawning an event loop.
        """
        if reality_enabled is None:
            try:
                if Path(settings.xray_config_path).exists():
                    import json
                    cfg = json.loads(Path(settings.xray_config_path).read_text(encoding="utf-8"))
                    reality_enabled = any(
                        (ib.get("streamSettings", {}).get("security") == "reality")
                        for ib in cfg.get("inbounds", [])
                    )
            except Exception:
                reality_enabled = None

        log.info("=" * 60)
        log.info("Spider Panel startup summary")
        log.info(f"  FastAPI port        : {settings.panel_port}")
        log.info(f"  Xray internal port  : {settings.xray_inbound_port}")
        log.info(f"  Railway TCP port    : {settings.RAILWAY_TCP_PROXY_PORT or '(not set)'}")
        log.info(f"  Active domain       : {active_domain or 'none'}")
        log.info(f"  Reality enabled     : {('yes' if reality_enabled else 'no') if reality_enabled is not None else 'unknown'}")
        log.info(f"  Xray binary         : {settings.XRAY_BINARY_PATH}")
        log.info("=" * 60)

    # -- control ----------------------------------------------------------
    async def ensure_stopped(self) -> None:
        self._stopping = True
        await self._stop_watchdog()
        pid = self._read_pid()
        if pid:
            await self._kill_pid(pid)
        if self._proc is not None:
            await self._kill_proc(self._proc)
            self._proc = None
        self._clear_pid()
        self._stopping = False

    @staticmethod
    async def _kill_pid(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            await asyncio.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    @staticmethod
    async def _kill_proc(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

    async def start(self) -> bool:
        """Start xray if not already running. Returns True on success.

        Refuses to start if the config fails `xray run -test`. On a crash we
        record full stdout/stderr and return False (so the dashboard can show
        the exact parser error), instead of silently swallowing returncode 23.
        """
        if self.is_running():
            log_xray("start.skip", reason="already_running")
            return True
        await self.ensure_stopped()

        binary = settings.XRAY_BINARY_PATH
        config = settings.xray_config_path
        if not Path(binary).exists():
            log_xray("start.fail", reason="binary_missing", binary=binary)
            self._last["error"] = f"binary missing: {binary}"
            return False
        if not Path(config).exists():
            log_xray("start.fail", reason="config_missing", config=config)
            self._last["error"] = f"config missing: {config}"
            return False

        # 1) validate first — never start with a broken config
        vr = self.validate_config_file(config)
        if not vr["ok"]:
            log_xray("start.fail", reason="config_validation", **{k: vr[k] for k in ("returncode", "message")})
            self._last["error"] = "config validation failed: " + vr["message"]
            return False

        # 2) spawn
        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary, "run", "-config", config,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._logdir),
            )
        except (OSError, ValueError) as e:
            log_xray("start.fail", reason=str(e))
            self._last["error"] = str(e)
            return False

        # 3) detect immediate crash (invalid config / port in use)
        await asyncio.sleep(1.0)
        if self._proc.returncode is not None:
            out, err = await self._proc.communicate()
            out_s = (out or b"").decode(errors="replace")
            err_s = (err or b"").decode(errors="replace")
            self._last["returncode"] = self._proc.returncode
            self._last["stdout"] = out_s
            self._last["stderr"] = err_s
            self._last["command"] = f"{binary} run -config {config}"
            self._last["cwd"] = str(self._logdir)
            self._last["error"] = (
                f"xray exited with code {self._proc.returncode}\n"
                f"--- stdout ---\n{out_s}\n--- stderr ---\n{err_s}"
            )
            # persist to log file so UI can show it
            self._append_log(f"[crash] returncode={self._proc.returncode}")
            for ln in (out_s + "\n" + err_s).splitlines():
                self._append_log(ln)
            self._proc = None
            log_xray("start.crash", returncode=self._last["returncode"], stderr=err_s[:2000])
            return False

        self._write_pid(self._proc.pid)
        self._last["error"] = None
        # Drain logs in background, then watch for unexpected death.
        asyncio.create_task(self._pump(self._proc))
        asyncio.create_task(self._watchdog())
        log_xray("start.ok", pid=self._proc.pid, config=config)
        return True

    async def _pump(self, proc: asyncio.subprocess.Process) -> None:
        async def _read(stream, level):
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode(errors="replace").rstrip()
                # also forward to app logger (tagged)
                log.info(f"xray[{level}]: {decoded}")
                self._append_log(f"{level}: {decoded}")

        await asyncio.gather(_read(proc.stdout, "out"), _read(proc.stderr, "err"))
        # process exited
        rc = await proc.wait()
        self._append_log(f"[exit] returncode={rc}")
        if self._read_pid() == proc.pid:
            self._clear_pid()
        if not self._stopping and self.is_running() is False:
            # unexpected death handled by watchdog; mark for restart
            self._last["returncode"] = rc

    async def _watchdog(self) -> None:
        """Restart Xray if it exits unexpectedly (crash)."""
        self._watchdog_task = asyncio.current_task()
        backoff = 1
        while not self._stopping:
            await asyncio.sleep(2)
            if self._stopping:
                return
            if self.is_running():
                backoff = 1
                continue
            # not running and we didn't stop it -> try restart
            if self._stopping:
                return
            log_xray("watchdog.restart", attempt=f"+{backoff}s")
            ok = await self.start()
            if ok:
                backoff = 1
            else:
                await asyncio.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    async def _stop_watchdog(self) -> None:
        self._stopping = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except (asyncio.CancelledError, Exception):
                pass
            self._watchdog_task = None

    async def stop(self) -> None:
        self._stopping = True
        await self._stop_watchdog()
        await self.ensure_stopped()
        log_xray("stop.ok")

    async def restart(self) -> bool:
        await self.ensure_stopped()
        return await self.start()

    async def reload(self) -> bool:
        """Reload config gracefully (SIGHUP) or restart."""
        pid = self._read_pid()
        if pid and self._proc is not None and self._proc.returncode is None:
            try:
                os.kill(pid, signal.SIGHUP)
                log_xray("reload.sighup", pid=pid)
                await asyncio.sleep(1.0)
                if self._proc.returncode is None:
                    return True
            except OSError:
                pass
        return await self.restart()

    def is_running(self) -> bool:
        pid = self._read_pid()
        if pid:
            return True
        if self._proc is not None and self._proc.returncode is None:
            return True
        return False

    def xray_version(self) -> str | None:
        """Return the installed Xray version string, or None if unavailable."""
        binary = settings.XRAY_BINARY_PATH
        if not Path(binary).exists():
            return None
        try:
            proc = subprocess.run(
                [binary, "version"], capture_output=True, text=True, timeout=10,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            if out:
                # first line looks like: "Xray 24.9.30 (Xray, Penetrates Everything.)"
                return out.splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            return None
        return None

    async def health_check(self) -> dict:
        running = self.is_running()
        return {
            "running": running,
            "pid": self._read_pid(),
            "binary": settings.XRAY_BINARY_PATH,
            "version": self.xray_version(),
            "config": settings.xray_config_path,
            "checked_at": time.time(),
            "auto_restart": True,
            "last_exit": self._last.get("returncode"),
            "last_error": self._last.get("error"),
        }

    def last_result(self) -> dict:
        """Return the most recent validation/start diagnostics for the UI."""
        return dict(self._last)

    def get_logs(self, limit: int = 200) -> list[str]:
        # prefer the file (persists across restarts), fall back to ring
        try:
            if self._logfile.exists():
                lines = self._logfile.read_text(encoding="utf-8", errors="replace").splitlines()
                return lines[-limit:]
        except OSError:
            pass
        return list(self._ring)[-limit:]


# Singleton
manager = XrayProcessManager()
