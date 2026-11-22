"""One durable page per Vercel Queue invocation, with optimistic publication.

Declare ``app.hosting.queue_worker:process_message`` in the Vercel subscriber
configuration. The SDK supplies the queue-only trigger and its authentication;
this module does not expose an HTTP handler or start a polling thread.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from vercel.queue import send, subscribe

from app.core.review import ReviewError
from app.hosting.workspace import HostedWorkspace
from app.services.review_jobs import JobRunner


TOPIC = "pdf-review-jobs"


def _pending(store):
    # ReviewStore permits one active job. Keep publication bounded even if an
    # older imported workspace contains several queued records.
    with store.connection() as db:
        row = db.execute(
            "SELECT id,cursor,attempt FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1"
        ).fetchone()
    if not row:
        return []
    return [{"job_id": row["id"], "cursor": row["cursor"], "attempt": row["attempt"]}]


async def _dispatch(payloads):
    for payload in payloads:
        # Repeated API requests, duplicate deliveries and a crash between state
        # commit and send all converge on the same next-page message.
        key = f"pdf-review:v1:{payload['job_id']}:{payload['cursor']}:{payload['attempt']}"
        await send(TOPIC, payload, idempotency_key=key)


async def dispatch_pending(store):
    """Publish queued work only after the caller's workspace.commit succeeds.

    Send failures intentionally propagate. The durable queued record survives;
    retrying publication is safe because the key includes its saved generation.
    """
    await _dispatch(_pending(store))


def _validated(message):
    if not isinstance(message, dict):
        raise ValueError("Queue payload must be an object")
    identifier = message.get("job_id")
    if not isinstance(identifier, str) or str(UUID(identifier)) != identifier:
        raise ValueError("Queue payload has an invalid job identifier")
    for field in ("cursor", "attempt"):
        if type(message.get(field)) is not int or message[field] < 0:
            raise ValueError(f"Queue payload has an invalid {field}")
    return {key: message[key] for key in ("job_id", "cursor", "attempt")}


def _process_unit(message):
    # Lazy runtime import avoids loading worker/model code during subscriber
    # discovery. No model is retained between requests.
    from app.hosting.runtime import HostedWorker, hosted_settings

    with HostedWorkspace() as workspace:
        store = workspace.store
        try:
            job = store.job(message["job_id"])
        except ReviewError as exc:
            if exc.code != "job_not_found":
                raise
            return _pending(store)
        if (job["status"] != "queued" or job["cursor"] != message["cursor"]
                or job["attempt"] != message["attempt"]):
            # A preceding invocation may have committed then crashed before
            # sending the continuation. Only redispatch the saved checkpoint.
            return _pending(store)
        if job["kind"] == "extract":
            workspace.ensure(f"originals/{job['document_id']}.pdf")
        settings = hosted_settings(workspace.root)
        worker = HostedWorker(settings)
        runner = JobRunner(store, worker, settings)
        runner.passwords = workspace.passwords
        with store.connection(True) as db:
            updated = db.execute(
                "UPDATE jobs SET status='running',attempt=attempt+1 WHERE id=? AND status='queued' AND cursor=? AND attempt=?",
                (message["job_id"], message["cursor"], message["attempt"]),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Queued checkpoint changed before processing")
        runner.run(store.job(job["id"]), max_units=1)
        # Page and job cursor publish together. A concurrent edit/pause/cancel
        # makes commit raise for Queue retry; its stale copy is discarded.
        workspace.commit()
        return _pending(store)


@subscribe(topic=TOPIC, max_concurrency=1, max_attempts=10, retry_after=60)
async def process_message(message):
    payload = _validated(message)
    pending = await asyncio.to_thread(_process_unit, payload)
    # If send fails, retry sees the saved next cursor and republishes it.
    await _dispatch(pending)
