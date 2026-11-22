"""Vercel-only resource settings; Windows defaults are never relaxed here."""
from pathlib import Path
import os
import psutil

from app.core.config import Settings
from app.core.review import ReviewError
from app.infrastructure.bounded_worker import BoundedWorker

# Bundled Linux executable is isolated from the normal Windows environment.
_native_bin = Path(__file__).resolve().parents[2] / "vendor" / "native" / "bin"
if _native_bin.is_dir():
    os.environ["PATH"] = str(_native_bin) + os.pathsep + os.environ.get("PATH", "")


def hosted_settings(root: Path):
    return Settings(_env_file=None, data_dir=root, runtime_mode="local",
        models_dir=Path(__file__).resolve().parents[2] / "models",
        max_upload_bytes=64 * 1024**2, max_pages=1000,
        worker_memory_mb=1280, minimum_free_memory_mb=256,
        worker_timeout_seconds=80, model_timeout_seconds=90,
        gemini_api_key=None, google_readonly_access_token=None)


def hosted_memory(settings):
    total = min(2048, int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "2048")))
    process = psutil.Process()
    used = sum(p.memory_info().rss for p in [process, *process.children(recursive=True)] if p.is_running()) // 1024**2
    available = max(0, min(psutil.virtual_memory().available // 1024**2, total - used))
    return {"available_mb": available, "total_mb": total, "required_mb": settings.minimum_free_memory_mb,
            "can_start_worker": available >= settings.minimum_free_memory_mb, "runtime": "vercel"}


class HostedWorker(BoundedWorker):
    def run(self, operation, payload, cancelled=lambda: False):
        memory = hosted_memory(self.settings)
        if not memory["can_start_worker"]:
            raise ReviewError("hosted_low_memory", "This Vercel invocation has insufficient free memory. Saved work is intact; retry this unit later or use a smaller region.", 503)
        return super().run(operation, payload, cancelled)
