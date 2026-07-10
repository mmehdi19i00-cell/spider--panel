"""Xray process lifecycle manager.

Design goals from the spec:
  * No zombie processes     -> we wait/reap on stop.
  * No duplicate processes  -> we check the pidfile + kill any stale xray.
  * Safe reload             -> `xray run -config ...` supports graceful reload
                               via the API; we also implement stop()->start().
  * Proper logging          -> stdout/stderr captured, tagged.

We track the child PID ourselves (pidfile) and never shell out to `killall`
blindly. On start we ensure a single instance.
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import log, log_xray


class XrayProcessManager:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._pidfile = Path(settings.data_dir) / "xray" / "xray.pid"

    # -- helpers ----------------------------------------------------------
    def _read_pid(self) -> int | None:
        try:
            pid = int(self._pidfile.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None
        # validate it is still our process
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

    # -- control ----------------------------------------------------------
    async def ensure_stopped(self) -> None:
        """Kill any running xray managed here or by a stale pidfile."""
        pid = self._read_pid()
        if pid:
            await self._kill_pid(pid)
        if self._proc is not None:
            await self._kill_proc(self._proc)
            self._proc = None
        self._clear_pid()

    @staticmethod
    async def _kill_pid(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        # wait up to 5s for graceful exit
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
        """Start xray if not already running. Returns True on success."""
        if self.is_running():
            log_xray("start.skip", reason="already_running")
            return True
        await self.ensure_stopped()

        binary = settings.XRAY_BINARY_PATH
        config = settings.xray_config_path
        if not os.path.exists(binary):
            log_xray("start.fail", reason="binary_missing", binary=binary)
            return False
        if not os.path.exists(config):
            log_xray("start.fail", reason="config_missing", config=config)
            return False

        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary,
                "run",
                "-config",
                config,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as e:
            log_xray("start.fail", reason=str(e))
            return False

        # Give it a moment; detect immediate crash (e.g. invalid config).
        await asyncio.sleep(1.0)
        if self._proc.returncode is not None:
            out, err = await self._proc.communicate()
            log_xray(
                "start.crash",
                returncode=self._proc.returncode,
                stderr=(err or b"").decode(errors="replace")[:2000],
            )
            self._proc = None
            return False

        self._write_pid(self._proc.pid)
        # Drain logs in the background so buffers don't block.
        asyncio.create_task(self._pump(self._proc))
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
                log.info(f"xray[{level}]: {line.decode(errors='replace').rstrip()}")

        await asyncio.gather(_read(proc.stdout, "out"), _read(proc.stderr, "err"))
        await proc.wait()
        # If we got here because of a crash after a successful start, clear pid.
        if self._read_pid() == proc.pid:
            self._clear_pid()

    async def stop(self) -> None:
        await self.ensure_stopped()
        log_xray("stop.ok")

    async def restart(self) -> bool:
        await self.ensure_stopped()
        return await self.start()

    async def reload(self) -> bool:
        """Safe reload: if xray supports SIGUSR1 reload we use it, else restart."""
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
        # Fallback: clean restart
        return await self.restart()

    def is_running(self) -> bool:
        pid = self._read_pid()
        if pid:
            return True
        if self._proc is not None and self._proc.returncode is None:
            return True
        return False

    async def health_check(self) -> dict:
        running = self.is_running()
        return {
            "running": running,
            "pid": self._read_pid(),
            "binary": settings.XRAY_BINARY_PATH,
            "config": settings.xray_config_path,
            "checked_at": time.time(),
        }


# Singleton
manager = XrayProcessManager()
