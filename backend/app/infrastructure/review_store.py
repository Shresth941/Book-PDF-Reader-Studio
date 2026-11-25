"""Transactional local review records. JSON page bodies keep all source evidence intact."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.core.review import ReviewError, checked_revision, page_blockers, region_blockers, translation_issues


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class ReviewStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("originals", "artifacts", "backups"):
            (self.root / name).mkdir(exist_ok=True)
        self.path = self.root / "review.sqlite3"
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL, target TEXT NOT NULL DEFAULT 'hi', domain TEXT NOT NULL DEFAULT 'general',
                    jurisdiction TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL REFERENCES documents(id), number INTEGER NOT NULL,
                    revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(document_id, number));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), kind TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS glossary (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), revision INTEGER NOT NULL,
                    format TEXT NOT NULL, mode TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, action TEXT NOT NULL,
                    body TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS form_previews (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    number INTEGER NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL,
                    UNIQUE(document_id,number));
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, doc, action, body):
        db.execute("INSERT INTO events(document_id,action,body,created) VALUES(?,?,?,?)", (doc, action, encode(body), now()))

    @staticmethod
    def _document(db, identifier):
        row = db.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ReviewError("not_found", "Document not found.", 404)
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    @staticmethod
    def _page(db, identifier, number):
        row = db.execute("SELECT body FROM pages WHERE document_id=? AND number=?", (identifier, number)).fetchone()
        if not row:
            raise ReviewError("page_not_found", "Page not found.", 404)
        return json.loads(row[0])

    @staticmethod
    def _glossary(db, identifier):
        target = db.execute("SELECT target,domain FROM documents WHERE id=?", (identifier,)).fetchone()
        direction = "en-hi" if target and target[0] == "hi" else "hi-en"
        entries = [json.loads(r[0]) for r in db.execute("SELECT body FROM glossary WHERE document_id=? ORDER BY rowid DESC", (identifier,))]
        precedence = {"document_definition": 0, "project": 1, "domain": 2, "general": 3}
        applicable, seen = [], set()
        for entry in sorted(entries, key=lambda e: precedence.get(e.get("scope"), 3)):
            key = (entry.get("source", "").casefold(), entry.get("sense", "").casefold())
            if entry.get("direction", direction) == direction and key not in seen:
                applicable.append(entry)
                seen.add(key)
        from app.services.legal_translation import is_legal, legal_glossary
        if target and is_legal(target[1]):
            applicable += [e for e in legal_glossary(target[0]) if not any(x["source"].casefold() == e["source"].casefold() for x in applicable)]
        return applicable

    def create_document(self, identifier, filename, sha256, inspection):
        with self.connection(True) as db:
            db.execute("INSERT INTO documents(id,filename,sha256,page_count,status,metadata,created) VALUES(?,?,?,?,?,?,?)",
                       (identifier, filename, sha256, inspection.get("page_count", 0), inspection["status"], encode(inspection), now()))
            for info in inspection.get("pages", []):
                page = {**info, "revision": 1, "status": "pending", "regions": [], "coverage_approved": False, "error": ""}
                db.execute("INSERT INTO pages VALUES(?,?,?,?)", (identifier, info["number"], 1, encode(page)))
            self.event(db, identifier, "import", {"sha256": sha256, "status": inspection["status"]})
        return self.document(identifier)

    def document(self, identifier):
        with self.connection() as db:
            doc = self._document(db, identifier)
            # Never return 1,000 page images or bodies in the library response.
            doc["metadata"] = {k: v for k, v in doc["metadata"].items() if k != "pages"}
            counts = {"extracted": 0, "coverage_approved": 0, "content_approved": 0}
            glossary = self._glossary(db, identifier)
            for row in db.execute("SELECT body FROM pages WHERE document_id=?", (identifier,)):
                page = json.loads(row[0])
                counts["extracted"] += page["status"] == "extracted"
                counts["coverage_approved"] += bool(page["coverage_approved"])
                counts["content_approved"] += not page_blockers(page, glossary, doc["target"])
            doc["progress"] = counts
            if doc["status"] == "ready":
                if counts["content_approved"] == doc["page_count"] and doc["page_count"]:
                    doc["review_status"] = "content_reviewed"
                elif counts["coverage_approved"]:
                    doc["review_status"] = "partially_reviewed"
                elif counts["extracted"] == doc["page_count"]:
                    doc["review_status"] = "ready_for_review"
                else:
                    doc["review_status"] = "needs_attention"
            else:
                doc["review_status"] = "needs_attention"
            return doc

    def documents(self):
        with self.connection() as db:
            # Library is capped; older items are still addressable by ID.
            ids = [r[0] for r in db.execute("SELECT id FROM documents ORDER BY created DESC LIMIT 100")]
        return [self.document(i) for i in ids]

    def page(self, identifier, number):
        with self.connection() as db:
            page = self._page(db, identifier, number)
            glossary = self._glossary(db, identifier)
            page["blockers"] = page_blockers(page, glossary, self._document(db, identifier)["target"])
            for region in page["regions"]:
                region["checks"] = translation_issues(region.get("source", ""), region.get("translation", ""), glossary) if region["disposition"] == "translate" else []
            return page

    def _save_page(self, db, identifier, page, action, detail):
        page.pop("blockers", None)
        for r in page["regions"]:
            r.pop("checks", None)
        page["revision"] += 1
        db.execute("UPDATE pages SET revision=?,body=? WHERE document_id=? AND number=?",
                   (page["revision"], encode(page), identifier, page["number"]))
        db.execute("UPDATE documents SET revision=revision+1 WHERE id=?", (identifier,))
        self.event(db, identifier, action, {"page": page["number"], "revision": page["revision"], **detail})

    def mutate_region(self, identifier, number, expected, region_id, changes, reviewer):
        with self.connection(True) as db:
            page = self._page(db, identifier, number)
            checked_revision(page["revision"], expected)
            region = next((r for r in page["regions"] if r["id"] == region_id), None)
            if not region:
                raise ReviewError("region_not_found", "Region not found.", 404)
            # Cross-page context is conservatively invalidated for all translated pages.
            old = dict(region)
            region.update(changes)
            region["source_approved"] = False
            region["translation_approved"] = False
            region["acknowledged"] = {}
            page["coverage_approved"] = False
            if region.get("table_id"):
                from app.core.structure_review import clear_table_approvals
                clear_table_approvals(page)
            context_changed = any(old.get(field) != region.get(field)
                                  for field in ("source", "source_language", "disposition", "kind", "contains_text"))
            if context_changed:
                self._clear_translation_approvals(page)
            self._save_page(db, identifier, page, "region_edit", {"region": region_id, "before": old, "after": region, "reviewer": reviewer})
            if context_changed:
                self._invalidate_translations(db, identifier, except_page=number)
        return self.page(identifier, number)

    def refresh_legacy_recognition(self, identifier, number, expected, recognition, reviewer):
        """Refresh decoding/OCR on the same native regions without replacing saved edits."""
        with self.connection(True) as db:
            page = self._page(db, identifier, number)
            checked_revision(page["revision"], expected)
            changed = []
            for region in page["regions"]:
                if region.get("method") != "native" or region.get("table_id"):
                    continue
                matches = [r for r in recognition["regions"] if r.get("method") == "native"
                           and r["raw"] == region["raw"] and r["bbox"] == region["bbox"]
                           and r.get("fonts") == region.get("fonts")
                           and r.get("alternatives")]
                if len(matches) != 1:
                    continue
                fresh = matches[0]
                if region.get("alternatives") == fresh["alternatives"] and region.get("text_layout") == fresh.get("text_layout"):
                    continue
                before = dict(region)
                untouched = (region["source"] in (region["raw"], "") and not region["translation"]
                             and not region["source_approved"] and not region["translation_approved"]
                             and not region.get("reason") and region["source_language"] == "uncertain"
                             and region["kind"] == fresh["kind"] and region["disposition"] in ("translate", "unreadable"))
                region["alternatives"] = fresh["alternatives"]
                if fresh.get("text_layout"):
                    region["text_layout"] = fresh["text_layout"]
                region["evidence"] = list(dict.fromkeys(region.get("evidence", []) + fresh["evidence"]))
                if untouched:
                    region.update(source=fresh["source"], disposition=fresh["disposition"], language_hint=fresh.get("language_hint", "uncertain"))
                changed.append({"region": region["id"], "before": before, "after": dict(region)})
            if changed:
                self._clear_translation_approvals(page)
                self._save_page(db, identifier, page, "recognition_refresh", {"regions": changed, "reviewer": reviewer})
                self._invalidate_translations(db, identifier, except_page=number)
        return self.page(identifier, number)

    def prepare_form_refresh(self, identifier, number, expected_page, expected_document, inspection):
        from app.core.form_refresh import validate_inspection, mapping_suggestions
        with self.connection(True) as db:
            document = self._document(db, identifier)
            page = self._page(db, identifier, number)
            checked_revision(document["revision"], expected_document)
            checked_revision(page["revision"], expected_page)
            if document["status"] != "ready" or page["status"] != "extracted":
                raise ReviewError("not_extracted", "Inspect and extract this page before refreshing its form evidence.")
            validate_inspection(page, inspection, document["sha256"])
            preview = {"id": str(uuid4()), "document_revision": expected_document, "page_revision": expected_page,
                       "inspection": inspection, "suggestions": mapping_suggestions(page, inspection)}
            db.execute("DELETE FROM form_previews WHERE document_id=? AND number=?", (identifier, number))
            db.execute("INSERT INTO form_previews VALUES(?,?,?,?,?)", (preview["id"], identifier, number, encode(preview), now()))
        return preview

    def apply_form_refresh(self, identifier, number, expected_page, expected_document, preview_id, bindings, note, reviewer, verified_sha256):
        from app.core.form_refresh import validate_inspection, merge_form_evidence
        with self.connection(True) as db:
            document = self._document(db, identifier)
            page = self._page(db, identifier, number)
            checked_revision(document["revision"], expected_document)
            checked_revision(page["revision"], expected_page)
            record = db.execute("SELECT body FROM form_previews WHERE id=? AND document_id=? AND number=?",
                                (preview_id, identifier, number)).fetchone()
            if not record:
                raise ReviewError("form_preview_missing", "Inspect the current form evidence again. This preview was replaced or already applied.", 409)
            preview = json.loads(record[0])
            checked_revision(document["revision"], preview["document_revision"])
            checked_revision(page["revision"], preview["page_revision"])
            if verified_sha256 != document["sha256"]:
                raise ReviewError("original_changed", "The original fingerprint changed; no evidence was applied.", 409)
            if document["status"] != "ready" or page["status"] != "extracted":
                raise ReviewError("not_extracted", "The page is no longer ready for evidence refresh.")
            if len(note.strip()) < 10:
                raise ReviewError("form_mapping_note", "Record how you checked the field mappings against the original.")
            validate_inspection(page, preview["inspection"], document["sha256"])
            result, changes, changed = merge_form_evidence(page, preview["inspection"], bindings)
            if changed:
                self._clear_translation_approvals(result)
                self._save_page(db, identifier, result, "form_evidence_refresh", {
                    "reviewer": reviewer, "note": note, "bindings": bindings, "changes": changes,
                    "previous_inventory": page.get("form_evidence"), "document_sha256": document["sha256"]})
                self._invalidate_translations(db, identifier, except_page=number)
            db.execute("DELETE FROM form_previews WHERE id=?", (preview_id,))
        return self.page(identifier, number)

    def add_region(self, identifier, number, expected, body, reviewer):
        with self.connection(True) as db:
            page = self._page(db, identifier, number)
            checked_revision(page["revision"], expected)
            x0, y0, x1, y1 = body["bbox"]
            if not (0 <= x0 < x1 <= page["width"] and 0 <= y0 < y1 <= page["height"]):
                raise ReviewError("invalid_region", "Draw a nonempty region inside this page.")
            region = {"id": str(uuid4()), "order": len(page["regions"]), "kind": "manual",
                      "raw": "", "source": "", "translation": "", "source_language": "uncertain",
                      "language_hint": "uncertain", "disposition": "unreadable", "contains_text": True,
                      "method": "manual", "alternatives": [], "evidence": [], "reason": "",
                      "source_approved": False, "translation_approved": False, "acknowledged": {}, **body}
            page["regions"].append(region)
            self._clear_translation_approvals(page)
            self._save_page(db, identifier, page, "region_add", {"region": region, "reviewer": reviewer})
            self._invalidate_translations(db, identifier, except_page=number)
        return self.page(identifier, number)

    def reorder_regions(self, identifier, number, expected, region_ids, reviewer):
        with self.connection(True) as db:
            page = self._page(db, identifier, number)
            checked_revision(page["revision"], expected)
            previous = [r["id"] for r in sorted(page["regions"], key=lambda r: r["order"])]
            if (not isinstance(region_ids, list) or any(not isinstance(i, str) for i in region_ids)
                    or len(region_ids) != len(previous) or len(set(region_ids)) != len(region_ids)
                    or set(region_ids) != set(previous)):
                raise ReviewError("invalid_reading_order", "Include every page region exactly once when changing the reading order.")
            if region_ids != previous:
                by_id = {r["id"]: r for r in page["regions"]}
                page["regions"] = [by_id[i] for i in region_ids]
                for order, region in enumerate(page["regions"]):
                    region["order"] = order
                self._clear_translation_approvals(page)
                self._save_page(db, identifier, page, "reading_order", {
                    "before": previous, "after": region_ids, "reviewer": reviewer,
                })
                # Changing sequence can change the meaning of surrounding clauses.
                self._invalidate_translations(db, identifier, except_page=number)
        return self.page(identifier, number)

    def approve(self, identifier, number, expected, kind, reviewer, region_id=None, acknowledgements=None):
        with self.connection(True) as db:
            page = self._page(db, identifier, number)
            checked_revision(page["revision"], expected)
            if kind == "coverage":
                if page["status"] != "extracted":
                    raise ReviewError("not_extracted", "Extract or explicitly transcribe this page before completeness review.")
                from app.core.form_refresh import form_inventory_blockers
                form_issues = form_inventory_blockers(page)
                if form_issues:
                    raise ReviewError("form_inventory_review", form_issues[0])
                from app.core.structure_review import structure_warning_fingerprint
                fingerprint = structure_warning_fingerprint(page)
                if fingerprint:
                    note = (acknowledgements or {}).get("structure_warnings", "").strip()
                    if len(note) < 10:
                        raise ReviewError("structure_warning_review", "Record how the table-detection warnings were checked against the whole original before approving completeness.")
                    page["structure_warning_review"] = {"fingerprint": fingerprint, "note": note, "reviewer": reviewer}
                page["coverage_approved"] = True
            else:
                region = next((r for r in page["regions"] if r["id"] == region_id), None)
                if not region:
                    raise ReviewError("region_not_found", "Region not found.", 404)
                if kind == "source":
                    if region.get("method") == "form-field" and not region.get("form_evidence", {}).get("provenance", {}).get("evidence_sha256"):
                        raise ReviewError("form_evidence_incomplete", "This field could not be inspected completely. Inspect and map its form evidence below; if it remains unresolved, obtain a static printable source before completing review.")
                    if region.get("method") == "form-field" and len(region.get("form_review_note", "").strip()) < 10:
                        raise ReviewError("form_reconciliation_required", "Compare the stored field value and visible appearance, then save a reconciliation note before approving the source.")
                    if region["disposition"] in ("translate", "already_target") and not region["source"].strip():
                        raise ReviewError("empty_source", "Transcribe the visible source first.")
                    if region["disposition"] == "already_target" and region.get("source_language") != self._document(db, identifier)["target"]:
                        raise ReviewError("already_target_language", "Confirm a source language matching the document target, or change this region's disposition to translate.")
                    region["source_approved"] = True
                    region.pop("form_evidence_changed", None)
                else:
                    if not region["source_approved"]:
                        raise ReviewError("source_unreviewed", "Review the source before the translation.")
                    region["acknowledged"] = acknowledgements or {}
                    region["translation_approved"] = True
                    blockers = region_blockers(region, self._glossary(db, identifier), self._document(db, identifier)["target"])
                    if blockers:
                        raise ReviewError("unresolved_checks", " ".join(blockers))
            self._save_page(db, identifier, page, "approve_" + kind, {"region": region_id, "reviewer": reviewer, "acknowledgements": acknowledgements or {}})
        return self.page(identifier, number)

    @staticmethod
    def _clear_translation_approvals(page, target_changed=False):
        from app.core.structure_review import clear_table_approvals
        clear_table_approvals(page)
        for region in page["regions"]:
            region["translation_approved"] = False
            region["acknowledged"] = {}
            if target_changed and region["disposition"] == "already_target":
                region["source_approved"] = False
        page["coverage_approved"] = False

    def create_manual_table(self, identifier, number, expected, body, reviewer):
        from app.core.manual_table import create_manual_table
        with self.connection(True) as db:
            document = self._document(db, identifier)
            page = self._page(db, identifier, number)
            checked_revision(page["revision"], expected)
            if document["status"] != "ready" or page["status"] != "extracted":
                raise ReviewError("not_extracted", "Inspect and extract this page before defining a table grid.")
            table = create_manual_table(page, body, reviewer)
            self._clear_translation_approvals(page)
            self._save_page(db, identifier, page, "manual_table_create", {
                "table_id": table["id"], "reviewer": reviewer, "definition": body,
                "original_regions": table["original_regions"],
                "original_proposal": table.get("original_proposal"),
            })
            self._invalidate_translations(db, identifier, except_page=number)
        return self.page(identifier, number)

    def review_table(self, identifier, number, expected, table_id, action, note, reviewer):
        from app.core.structure_review import decide_table
        with self.connection(True) as db:
            page = self._page(db, identifier, number)
            checked_revision(page["revision"], expected)
            # Validate and stage changes before invalidating existing approvals.
            decide_table(page, table_id, action, note, reviewer)
            if action == "adopt":
                self._clear_translation_approvals(page)
                self._invalidate_translations(db, identifier, except_page=number)
            page["coverage_approved"] = False
            self._save_page(db, identifier, page, "table_review", {
                "table_id": table_id, "action": action, "note": note, "reviewer": reviewer,
            })
        return self.page(identifier, number)

    def _invalidate_translations(self, db, identifier, except_page=None, target_changed=False):
        for row in db.execute("SELECT number,body FROM pages WHERE document_id=?", (identifier,)).fetchall():
            if row["number"] == except_page:
                continue
            page = json.loads(row["body"])
            self._clear_translation_approvals(page, target_changed=target_changed)
            page["revision"] += 1
            db.execute("UPDATE pages SET revision=?,body=? WHERE document_id=? AND number=?", (page["revision"], encode(page), identifier, page["number"]))

    def update_context(self, identifier, expected, values):
        with self.connection(True) as db:
            previous = self._document(db, identifier)
            checked_revision(previous["revision"], expected)
            db.execute("UPDATE documents SET target=?,domain=?,jurisdiction=?,revision=revision+1 WHERE id=?", (values["target"], values["domain"], values["jurisdiction"], identifier))
            self._invalidate_translations(db, identifier, target_changed=previous["target"] != values["target"])
            self.event(db, identifier, "context", values)
        return self.document(identifier)

    def glossary(self, identifier):
        with self.connection() as db:
            return self._glossary(db, identifier)

    def add_glossary(self, identifier, expected, body):
        with self.connection(True) as db:
            checked_revision(self._document(db, identifier)["revision"], expected)
            entry = {**body, "id": str(uuid4())}
            db.execute("INSERT INTO glossary VALUES(?,?,?)", (entry["id"], identifier, encode(entry)))
            db.execute("UPDATE documents SET revision=revision+1 WHERE id=?", (identifier,))
            self._invalidate_translations(db, identifier)
            self.event(db, identifier, "glossary_add", entry)
        return entry

    def enqueue(self, identifier, kind, payload):
        with self.connection(True) as db:
            doc = self._document(db, identifier)
            if doc["status"] != "ready":
                raise ReviewError("document_not_ready", "Resolve document inspection before processing.")
            active = db.execute("SELECT id FROM jobs WHERE status IN ('queued','running')").fetchone()
            if active:
                raise ReviewError("busy", "A document job is active. Pause or finish it first.", 409)
            job_id = str(uuid4())
            db.execute("INSERT INTO jobs(id,document_id,kind,status,payload,created) VALUES(?,?,?,?,?,?)", (job_id, identifier, kind, "queued", encode(payload), now()))
        return self.job(job_id)

    def job(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ReviewError("job_not_found", "Job not found.", 404)
            return {**dict(row), "payload": json.loads(row["payload"])}

    def jobs(self, identifier):
        with self.connection() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE document_id=? ORDER BY created DESC LIMIT 20", (identifier,))]
        return [self.job(i) for i in ids]

    def control(self, job_id, action):
        with self.connection(True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ReviewError("job_not_found", "Job not found.", 404)
            if row["status"] in ("completed", "cancelled"):
                raise ReviewError("job_finished", "This job is already finished.", 409)
            if action == "resume" and db.execute("SELECT id FROM jobs WHERE id<>? AND status IN ('queued','running')", (job_id,)).fetchone():
                raise ReviewError("busy", "Finish or pause the other active job first.", 409)
            status = {"pause": "paused", "cancel": "cancelled", "resume": "queued"}[action]
            db.execute("UPDATE jobs SET status=?,attempt=attempt+1,message='' WHERE id=?", (status, job_id))
        return self.job(job_id)

    def recover(self):
        with self.connection(True) as db:
            db.execute("UPDATE jobs SET status='paused',attempt=attempt+1,message='Interrupted. Resume from the last committed page.' WHERE status IN ('running','queued')")

    def snapshot(self, identifier, expected):
        with self.connection(True) as db:
            doc = self._document(db, identifier)
            checked_revision(doc["revision"], expected)
            pages = [json.loads(r[0]) for r in db.execute("SELECT body FROM pages WHERE document_id=? ORDER BY number", (identifier,))]
            glossary = self._glossary(db, identifier)
            blockers = [f"Page {p['number']}: {m}" for p in pages for m in page_blockers(p, glossary, doc["target"])]
            if not pages or len(pages) != doc["page_count"] or doc["status"] != "ready":
                blockers.append("The page inventory is incomplete or inspection has not passed.")
            return {"document": doc, "pages": pages, "glossary": glossary, "blockers": blockers}

    def checkpoint(self, job_id, attempt, number, expected_page, result, expected_document=None):
        with self.connection(True) as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job["status"] != "running" or job["attempt"] != attempt:
                raise ReviewError("superseded", "This job attempt has been superseded.", 409)
            identifier = job["document_id"]
            if expected_document is not None:
                checked_revision(self._document(db, identifier)["revision"], expected_document)
            old = self._page(db, identifier, number)
            checked_revision(old["revision"], expected_page)
            if job["kind"] == "extract" and old["regions"]:
                raise ReviewError("existing_page_content", "This page contains saved regions. Complete its manual transcription; extraction cannot overwrite existing work.", 409)
            result["revision"] = old["revision"]
            self._save_page(db, identifier, result, "job_checkpoint", {"job": job_id, "attempt": attempt})
            db.execute("UPDATE jobs SET cursor=cursor+1 WHERE id=?", (job_id,))
