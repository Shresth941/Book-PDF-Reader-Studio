"""A fresh process for every heavy unit, including PDF preflight.

The child waits on stdin until Windows Job Object limits have been attached.
The parent also monitors the complete process tree, wall time and cancellation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import psutil
from app.core.review import ReviewError

_gate = threading.Lock()


def memory_status(settings):
    memory = psutil.virtual_memory()
    required = settings.minimum_free_memory_mb
    return {"available_mb": memory.available // 1024**2,
            "total_mb": memory.total // 1024**2, "required_mb": required,
            "can_start_worker": memory.available >= required * 1024**2}


def windows_job(process, memory_bytes):
    if os.name != "nt":
        return None
    import ctypes as c
    from ctypes import wintypes as w
    class Basic(c.Structure):
        _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                    ("min_ws", c.c_size_t), ("max_ws", c.c_size_t), ("active", w.DWORD),
                    ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ("ro", "wo", "oo", "rb", "wb", "ob")]
    class Extended(c.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", c.c_size_t),
                    ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000 | 0x200  # KILL_ON_JOB_CLOSE | JOB_MEMORY
    limits.job_memory = memory_bytes
    if not handle or not kernel.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        if handle:
            kernel.CloseHandle(handle)
        raise ReviewError("worker_limits_unavailable", "Windows could not apply worker memory limits. Processing was not started.", 503)
    return lambda: kernel.CloseHandle(handle)


class BoundedWorker:
    def __init__(self, settings):
        self.settings = settings

    def run(self, operation: str, payload: dict, cancelled=lambda: False):
        with _gate:
            if cancelled():
                raise ReviewError("cancelled", "Processing was paused or cancelled.", 409)
            memory = memory_status(self.settings)
            if not memory["can_start_worker"]:
                raise ReviewError("low_memory", f"Available RAM is {memory['available_mb']} MB; at least {memory['required_mb']} MB is required to start processing. "
                    "The original is saved. Close unused applications or browser tabs, then retry inspection or resume the paused job.", 503)
            timeout = self.settings.model_timeout_seconds if operation in ("translate", "docling") else self.settings.worker_timeout_seconds
            limit = self.settings.worker_memory_mb * 1024**2
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "OMP_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false"}
            tessdata = self.settings.models_dir.resolve() / "tessdata"
            if (tessdata / "hin.traineddata").is_file() and (tessdata / "eng.traineddata").is_file():
                env["TESSDATA_PREFIX"] = str(tessdata)
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                process = subprocess.Popen([sys.executable, "-m", "app.infrastructure.worker"],
                    stdin=subprocess.PIPE, stdout=output, stderr=errors, env=env,
                    cwd=Path(__file__).resolve().parents[2],
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                close_job = None
                try:
                    close_job = windows_job(process, limit)
                    message = {"operation": operation, "payload": payload, "memory_bytes": limit}
                    process.stdin.write(json.dumps(message).encode("utf-8"))
                    process.stdin.close()
                    started = time.monotonic()
                    while process.poll() is None:
                        if cancelled():
                            raise ReviewError("cancelled", "Processing was paused or cancelled.", 409)
                        if time.monotonic() - started > timeout:
                            raise ReviewError("worker_timeout", "This processing unit exceeded its time limit. Try a smaller region or transcribe it manually.", 503)
                        if errors.tell() > 16 * 1024**2:
                            raise ReviewError("worker_log_limit", "Worker diagnostics exceeded their limit. No content was replaced.", 503)
                        try:
                            parent = psutil.Process(process.pid)
                            memory = sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)] if p.is_running())
                            if memory > limit:
                                raise ReviewError("worker_memory", "This unit exceeded its memory budget. Try a smaller region.", 503)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        time.sleep(0.1)
                    output.seek(0)
                    data = output.read(32 * 1024**2 + 1)
                    if process.returncode or len(data) > 32 * 1024**2:
                        raise ReviewError("worker_failed", "The isolated worker stopped or exceeded its result limit. The original and saved work are intact.", 503)
                    try:
                        result = json.loads(data)
                    except (ValueError, UnicodeError) as exc:
                        raise ReviewError("invalid_worker_result", "Worker returned an invalid result; no page was replaced.", 503) from exc
                    if result.get("error"):
                        raise ReviewError(result["error"], result["message"], result.get("status", 422))
                    return result
                finally:
                    if process.poll() is None:
                        try:
                            for child in psutil.Process(process.pid).children(recursive=True):
                                child.kill()
                        except psutil.Error:
                            pass
                        process.kill()
                    process.wait(timeout=10)
                    if close_job:
                        close_job()
