"""Single durable job consumer; checkpoints reject cancelled and stale work."""
import threading
import logging
import traceback
from app.core.review import ReviewError
from app.infrastructure.review_store import encode


class JobRunner:
    def __init__(self, store, worker, settings):
        self.store, self.worker, self.settings = store, worker, settings
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.passwords = {}  # Session-only; never write PDF passwords to logs/SQLite.
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.store.recover()
        self.stop.clear()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="pdf-review-jobs")
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=12)

    def loop(self):
        while not self.stop.is_set():
            with self.store.connection(True) as db:
                row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='running',attempt=attempt+1 WHERE id=?", (row[0],))
            if not row:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            self.run(self.store.job(row[0]))

    def run(self, job, *, max_units=None):
        """Optionally yield after bounded page checkpoints; local default is all."""
        if max_units is not None and (type(max_units) is not int or max_units < 1):
            raise ValueError("max_units must be a positive integer")
        identifier, job_id, attempt = job["document_id"], job["id"], job["attempt"]
        def cancelled():
            current = self.store.job(job_id)
            return self.stop.is_set() or current["status"] != "running" or current["attempt"] != attempt
        try:
            numbers = job["payload"]["pages"]
            pending = numbers[job["cursor"]:]
            if max_units is not None:
                pending = pending[:max_units]
            for number in pending:
                if cancelled():
                    return
                doc = self.store.document(identifier)
                page = self.store.page(identifier, number)
                if job["kind"] == "extract":
                    # Resume skips committed pages; explicit re-extraction must never erase edits.
                    if page["status"] == "extracted":
                        with self.store.connection(True) as db:
                            db.execute("UPDATE jobs SET cursor=cursor+1 WHERE id=? AND attempt=? AND status='running'", (job_id, attempt))
                        continue
                    if page["regions"]:
                        raise ReviewError("existing_page_content", f"Page {number} contains saved regions. Complete its manual transcription before continuing; extraction cannot overwrite existing work.", 409)
                    payload = {"path": str(self.store.root / "originals" / f"{identifier}.pdf"), "number": number,
                               "password": self.passwords.get(identifier, "")}
                    try:
                        result = self.worker.run("extract", payload, cancelled)
                    except ReviewError as exc:
                        if exc.code not in ("worker_timeout", "worker_memory"):
                            raise
                        result = self.worker.run("extract", {**payload, "retry": True}, cancelled)
                    result = {**page, **result}
                else:
                    regions = [r for r in page["regions"] if r["disposition"] == "translate" and not r["translation_approved"]]
                    if any(not r["source_approved"] for r in regions):
                        raise ReviewError("source_unreviewed", f"Review source regions on page {number} before translation.")
                    result = {**page, "coverage_approved": False}
                    # Language batches share one model at a time and no concurrent heavyweight instances.
                    for language in sorted({r["source_language"] for r in regions}):
                        output = self.worker.run("translate", {"models_dir": str(self.settings.models_dir.resolve()),
                            "regions": [r for r in regions if r["source_language"] == language], "target": doc["target"],
                            "domain": doc["domain"], "jurisdiction": doc["jurisdiction"],
                            "glossary": self.store.glossary(identifier)}, cancelled)
                        by_id = {r["id"]: r for r in output["regions"]}
                        result["regions"] = [{**r, **by_id[r["id"]], "translation_approved": False, "acknowledged": {}} if r["id"] in by_id else r for r in result["regions"]]
                self.store.checkpoint(job_id, attempt, number, page["revision"], result, doc["revision"])
            with self.store.connection(True) as db:
                db.execute("UPDATE jobs SET status=CASE WHEN cursor>=? THEN 'completed' ELSE 'queued' END,message='' WHERE id=? AND attempt=? AND status='running'", (len(numbers), job_id, attempt))
        except ReviewError as exc:
            with self.store.connection(True) as db:
                db.execute("UPDATE jobs SET status='paused',message=? WHERE id=? AND attempt=? AND status='running'", (f"{exc.code}: {exc.message}", job_id, attempt))
        except Exception as exc:
            logging.getLogger(__name__).error("Job implementation error (%s):\n%s", type(exc).__name__, "".join(traceback.format_tb(exc.__traceback__)))
            with self.store.connection(True) as db:
                db.execute("UPDATE jobs SET status='paused',message='Unexpected worker failure. Saved progress is intact; retry or review manually.' WHERE id=? AND attempt=? AND status='running'", (job_id, attempt))
