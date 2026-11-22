import React, { useEffect, useRef, useState } from "react";
import { reviewCall as api, reviewUrl, ReviewDocument, ReviewPage, ReviewJob, ReviewExport, Region, Capabilities } from "./reviewApi";
import "./review.css";
import {TableReviewPanel} from "./TableReviewPanel";
import {MemoryStatus} from "./MemoryStatus";
import {FormEvidenceReview, FormEvidencePreview} from "./FormEvidenceReview";
import {ManualTableEditor} from "./ManualTableEditor";
import {restoreRegionDraft} from "./regionDraft";
import {useReviewNotes} from "./useReviewNotes";
import {hostedUpload} from "./hostedUpload";

const reviewer = "Local reviewer";
const label = (s: string) => s.replaceAll("_", " ");
const pageCount = (d: ReviewDocument) => d.page_count ? `${d.page_count} pages` : "Page count pending inspection";

export function ReviewWorkspace({runtime = "local", initialDocumentId, onExit}: {runtime?: "local" | "vercel"; initialDocumentId?: string; onExit?: (id?: string) => void} = {}) {
  const hosted = runtime === "vercel";
  const [library, setLibrary] = useState<ReviewDocument[]>([]);
  const [doc, setDoc] = useState<ReviewDocument>();
  const [page, setPage] = useState<ReviewPage>();
  const [number, setNumber] = useState(1);
  const [region, setRegion] = useState<Region>();
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false); dirtyRef.current = dirty;
  const activeView = useRef({id: "", number: 1});
  const refreshSequence = useRef(0);
  const editGeneration = useRef(0);
  const busyRef = useRef(false);
  const [gridDirty, setGridDirty] = useState(false);
  const gridDirtyRef = useRef(false); gridDirtyRef.current = gridDirty;
  const [jobs, setJobs] = useState<ReviewJob[]>([]);
  const [exports, setExports] = useState<ReviewExport[]>([]);
  const [artifact, setArtifact] = useState<ReviewExport>();
  const [exportPage, setExportPage] = useState(1);
  const [caps, setCaps] = useState<Capabilities>();
  const [message, setMessage] = useState(hosted ? "Open a PDF to begin. Originals and review progress are saved in your private Vercel workspace." : "Open a PDF to begin. Originals and review progress stay on this computer.");
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [anchor, setAnchor] = useState<[number, number]>();
  const [corner, setCorner] = useState<[number, number]>();
  const [format, setFormat] = useState("pdf");
  const [mode, setMode] = useState("readable");
  const [target, setTarget] = useState("hi");
  const [domain, setDomain] = useState("general");
  const [jurisdiction, setJurisdiction] = useState("");
  const [password, setPassword] = useState("");
  const [glossary, setGlossary] = useState<Record<string, string>[]>([]);
  const [term, setTerm] = useState({source: "", target: "", sense: "", reference: ""});
  const [imageError, setImageError] = useState(false);
  const [exportImageError, setExportImageError] = useState(false);
  const [exportImageReady, setExportImageReady] = useState(false);
  const [manualReason, setManualReason] = useState("");
  const [cloudRequest, setCloudRequest] = useState<{id: string; payload_sha256: string; payload: unknown; notice: string}>();
  const [cloudEligible, setCloudEligible] = useState(false);

  const docPath = doc ? `/documents/${doc.id}` : "";
  const pagePath = `${docPath}/pages/${number}`;
  const meaningNotes = useReviewNotes(region ? `pdf-review-meaning:${doc?.id}:${number}:${region.id}` : "", page?.revision ?? 0, region?.acknowledged ?? {});
  const coverageNotes = useReviewNotes(page ? `pdf-review-coverage:${doc?.id}:${number}` : "", page?.revision ?? 0, {note: page?.structure_warning_review?.note ?? ""});
  const ack = meaningNotes.value, structureNote = coverageNotes.value.note ?? "";
  const noteStorageBlocked = meaningNotes.storageFailed || coverageNotes.storageFailed;
  const notesDirtyRef = useRef(false); notesDirtyRef.current = meaningNotes.dirty || coverageNotes.dirty;
  function beginMutation() { busyRef.current = true; ++refreshSequence.current; setBusy(true); }
  function finishMutation() { busyRef.current = false; setBusy(false); }
  async function run(action: () => Promise<void>, success?: string) {
    if (busyRef.current) return;
    beginMutation(); setError(false);
    try { await action(); if (success) setMessage(success); }
    catch (e) { setError(true); setMessage(e instanceof Error ? e.message : "Unable to complete this action."); }
    finally { finishMutation(); }
  }
  async function loadLibrary() { setLibrary((await api<{documents: ReviewDocument[]}>("/documents")).documents); }
  async function refresh(id: string, n: number, includePage = true) {
    if (activeView.current.id !== id || activeView.current.number !== n) return;
    const sequence = ++refreshSequence.current;
    const generation = editGeneration.current;
    const isCurrent = () => sequence === refreshSequence.current && activeView.current.id === id && activeView.current.number === n;
    const base = `/documents/${id}`;
    const [d, j, e, g] = await Promise.all([
      api<ReviewDocument>(base), api<{jobs: ReviewJob[]}>(`${base}/jobs`),
      api<{exports: ReviewExport[]}>(`${base}/exports`), api<{entries: Record<string, string>[]}>(`${base}/glossary`),
    ]);
    if (!isCurrent()) return;
    if (includePage && d.page_count && !dirtyRef.current) {
      const p = await api<ReviewPage>(`${base}/pages/${n}`);
      if (!isCurrent()) return;
      if (!dirtyRef.current && generation === editGeneration.current) {
        setPage(p);
        setRegion(previous => p.regions.find(r => r.id === previous?.id) ?? p.regions[0]);
      }
    }
    setDoc(d); setJobs(j.jobs); setExports(e.exports); setGlossary(g.entries);
    setLibrary(previous => previous.map(item => item.id === d.id ? d : item));
    setArtifact(previous => e.exports.find(x => x.id === previous?.id));
  }
  useEffect(() => { void run(async () => { await loadLibrary(); setCaps(await api<Capabilities>("/capabilities"));
    if (initialDocumentId) await selectDocument(await api<ReviewDocument>(`/documents/${initialDocumentId}`));
  }); }, []);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => { if (dirtyRef.current || gridDirtyRef.current || notesDirtyRef.current) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);
  useEffect(() => {
    if (!doc || !jobs.some(j => ["running", "queued"].includes(j.status))) return;
    let cancelled = false, inFlight = false;
    const timer = window.setInterval(() => {
      if (cancelled || inFlight || busyRef.current) return;
      inFlight = true;
      void refresh(doc.id, number, true).catch(e => {
        if (!cancelled && activeView.current.id === doc.id && activeView.current.number === number) { setError(true); setMessage(e.message); }
      }).finally(() => { inFlight = false; });
    }, hosted ? 20000 : 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [doc?.id, number, jobs.some(j => ["running", "queued"].includes(j.status))]);
  useEffect(() => { setImageError(false); }, [doc?.id, number]);
  useEffect(() => { setExportImageError(false); setExportImageReady(false); }, [artifact?.id, exportPage]);
  const draftKey = (id: string) => `pdf-review-draft:${doc?.id}:${number}:${id}`;
  useEffect(() => {
    if (!region || !page || dirty) return;
    try {
      const saved = localStorage.getItem(draftKey(region.id));
      if (saved) {
        const draft = JSON.parse(saved) as {region: Region; revision: number};
        setRegion(restoreRegionDraft(region, draft.region));
        ++editGeneration.current; dirtyRef.current = true; setDirty(true);
        setMessage(draft.revision === page.revision ? "Recovered unsaved region edits. Review and save them." : "Recovered unsaved region edits from an earlier revision. Compare them carefully with the current page before saving.");
      }
    } catch { /* A blocked browser store does not prevent server-side saving. */ }
  }, [region?.id]);

  async function selectDocument(d: ReviewDocument) {
    if (noteStorageBlocked) { setMessage("Browser storage could not retain your review notes. Approve or discard them before leaving this page."); return; }
    if (gridDirtyRef.current) { setMessage("Create or discard the unfinished table grid before opening another document."); return; }
    if (dirtyRef.current) { setMessage("Save or discard your region edits before opening another document."); return; }
    activeView.current = {id: d.id, number: 1};
    setPage(undefined); setRegion(undefined); setArtifact(undefined); setNumber(1);
    setTarget(d.target); setDomain(d.domain); setJurisdiction(d.jurisdiction);
    await refresh(d.id, 1);
  }
  async function navigate(n: number) {
    if (noteStorageBlocked) { setMessage("Browser storage could not retain your review notes. Approve or discard them before leaving this page."); return; }
    if (gridDirtyRef.current) { setMessage("Create or discard the unfinished table grid before changing pages."); return; }
    if (!doc || dirtyRef.current) { setMessage("Save or discard your region edits before changing pages."); return; }
    activeView.current = {id: doc.id, number: n};
    setPage(undefined); setRegion(undefined); setNumber(n); setArtifact(undefined);
    await refresh(doc.id, n);
  }
  function editRegion(changes: Partial<Region>) {
    if (region && page && !busyRef.current) {
      const updated = {...region, ...changes};
      ++editGeneration.current; dirtyRef.current = true;
      setRegion(updated); setDirty(true);
      try { localStorage.setItem(draftKey(region.id), JSON.stringify({region: updated, revision: page.revision})); }
      catch { setMessage("Browser draft storage is unavailable. Keep this tab open and save your edits to the workspace."); }
    }
  }
  async function saveRegion() {
    if (!region || !page || !doc) return;
    const generation = editGeneration.current;
    const {source, translation, source_language, disposition, contains_text, reason, kind} = region;
    const p = await api<ReviewPage>(`${pagePath}/regions/${region.id}`, "PATCH", {
      revision: page.revision, reviewer, source, translation, source_language, disposition, contains_text, reason, kind,
      form_review_note: region.form_review_note ?? "",
    });
    if (generation !== editGeneration.current) {
      setPage(p);
      setMessage("The submitted changes were saved. Newer edits remain unsaved; review and save them too.");
      return;
    }
    try { localStorage.removeItem(draftKey(region.id)); } catch { /* browser storage unavailable */ }
    setDirty(false); dirtyRef.current = false; setPage(p); setRegion(p.regions.find(r => r.id === region.id));
    await refresh(doc.id, number, false);
    setMessage("Changes saved. Review approvals for this content again.");
  }
  async function approve(kind: "source" | "translation" | "coverage") {
    if (!page || !doc) return;
    const p = await api<ReviewPage>(`${pagePath}/approvals`, "POST", {
      revision: page.revision, reviewer, kind, region_id: kind === "coverage" ? undefined : region?.id, acknowledgements: {...ack, structure_warnings: structureNote},
    });
    if (kind === "translation") meaningNotes.clear();
    if (kind === "coverage") coverageNotes.clear();
    setPage(p); setRegion(p.regions.find(r => r.id === region?.id)); await refresh(doc.id, number, false);
  }
  async function moveRegion(direction: -1 | 1) {
    if (!page || !doc || !region || dirty) return;
    const ids = page.regions.map(r => r.id);
    const index = ids.indexOf(region.id), destination = index + direction;
    if (index < 0 || destination < 0 || destination >= ids.length) return;
    [ids[index], ids[destination]] = [ids[destination], ids[index]];
    const p = await api<ReviewPage>(`${pagePath}/reading-order`, "POST", {
      revision: page.revision, reviewer, region_ids: ids,
    });
    setPage(p);
    if (!dirtyRef.current) setRegion(p.regions.find(r => r.id === region.id));
    await refresh(doc.id, number, false);
  }
  function point(e: React.PointerEvent<SVGSVGElement>): [number, number] {
    const rect = e.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(page!.width, (e.clientX - rect.left) / rect.width * page!.width)),
            Math.max(0, Math.min(page!.height, (e.clientY - rect.top) / rect.height * page!.height))];
  }
  async function finishDraw(e: React.PointerEvent<SVGSVGElement>) {
    if (!anchor || !page) return;
    const end = point(e), start = anchor;
    setAnchor(undefined); setCorner(undefined); setDrawing(false);
    if (Math.abs(end[0] - start[0]) < 2 || Math.abs(end[1] - start[1]) < 2) return;
    await run(async () => {
      const p = await api<ReviewPage>(`${pagePath}/regions`, "POST", {revision: page.revision, reviewer,
        bbox: [Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])]});
      setPage(p); setRegion(p.regions[p.regions.length - 1]); if (doc) await refresh(doc.id, number, false);
    }, "Missing region added. Transcribe and review it against the original.");
  }
  async function startJob(kind: string, all = false) {
    await api(`${docPath}/jobs`, "POST", {kind, start: all ? 1 : number, end: all ? doc!.page_count : number});
    await refresh(doc!.id, number);
  }
  const requiredDirections = [...new Set((page?.regions ?? [])
    .filter(r => r.disposition === "translate" && !r.translation_approved
      && ["en", "hi"].includes(r.source_language) && r.source_language !== doc?.target)
    .map(r => `${r.source_language}-${doc?.target}`))];
  const unavailableDirections = requiredDirections.filter(direction => caps?.translation[direction]?.available === false);

  return <div className="review-app">
    <header className="review-header"><div><span className="review-eyebrow">LOCAL DOCUMENT WORKSPACE</span><h1>PDF Reader Studio</h1><p>Read carefully. Translate in context. Review every page.</p></div>
      <div className="review-header-actions">{onExit && <button disabled={busy || dirty || gridDirty || notesDirtyRef.current || noteStorageBlocked} onClick={() => onExit(doc?.id)}>Back to simple reader</button>}<span className="local-pill">● {hosted ? "Private Vercel workspace" : "Local processing"}</span>
      <button disabled={busy} onClick={() => void run(async () => { const b = await api<{id: string}>("/backups", "POST"); window.location.assign(reviewUrl(`/backups/${b.id}`)); }, "Backup prepared with checksums.")}>Back up workspace</button></div>
    </header>
    <div className={`review-message ${error ? "is-error" : ""}`} role={error ? "alert" : "status"}>{busy ? "Working… " : ""}{message}</div>
    <main className="review-shell">
      <aside className="review-library">
        <label className="upload-label">Import PDF<input type="file" accept="application/pdf,.pdf" disabled={busy || dirty || gridDirty || noteStorageBlocked} onChange={e => {
          const file = e.target.files?.[0]; if (!file) return;
          void run(async () => { const form = new FormData(); form.append("file", file); const d = hosted ? await hostedUpload(file) : await api<ReviewDocument>("/documents", "POST", form); await loadLibrary(); await selectDocument(d); }, "Original saved. Review inspection status, then extract the pages."); e.target.value = "";
        }}/></label>
        <h2>Your documents</h2>
        {!library.length && <p className="muted">Imported PDFs and review progress appear here.</p>}
        {library.map(d => <button className={`library-item ${d.id === doc?.id ? "selected" : ""}`} key={d.id} disabled={busy || dirty} onClick={() => void run(() => selectDocument(d))}><strong>{d.filename}</strong><small>{pageCount(d)} · {label(d.review_status)}</small></button>)}
        {hosted && <p className="muted">Up to 64 MB per PDF. Hosted progress refreshes every 20 seconds to conserve the free storage allowance.</p>}
        <details className="capabilities"><summary>{hosted ? "Hosted capabilities" : "Local capabilities"}</summary>
          <p>OCR: {caps?.ocr.installed ? "Tesseract found; hin + eng data required." : "Tesseract is not installed."}</p>
          {Object.entries(caps?.translation ?? {}).map(([k, v]) => <p key={k}><strong>{k}</strong>: {v.available ? v.draft_only ? "Draft model ready. Human review required." : "Model configured" : v.reason}{v.available && v.model && <small> {v.model}</small>}</p>)}
          <p>{caps?.structure.reason}</p><p>{caps?.cloud.reason}</p>
        </details>
      </aside>
      <section className="review-main">
        {!doc ? <div className="review-empty"><span>01 / IMPORT</span><h2>A clear path from original to reviewed translation.</h2><p>Keep the original beside your work. Identify missing content, correct recognition, review meaning, and inspect the exported pages.</p><div className="step-row"><span>Original</span><span>Content review</span><span>Translation</span><span>Export review</span></div></div> : <>
          <section className="document-summary"><div><h2>{doc.filename}</h2><p>{pageCount(doc)} · Revision {doc.revision} · {label(doc.review_status)}</p></div>
            <a href={reviewUrl(`${docPath}/original`)} target="_blank" rel="noreferrer">Download original ↗</a>
            <div className="progress-row"><span><b>{doc.progress.extracted}</b> extracted</span><span><b>{doc.progress.coverage_approved}</b> completeness reviews</span><span><b>{doc.progress.content_approved}</b> content-approved pages</span></div>
          </section>
          {doc.status !== "ready" && <section className="review-warning"><h3>Inspection needs attention</h3><p>{doc.metadata.message}</p>
            {doc.metadata.code === "low_memory" && <MemoryStatus/>}
            <p>The page count becomes available after inspection succeeds. A password is needed only for a password-protected PDF.</p>
            <label>PDF password, if required<input type="password" value={password} onChange={e => setPassword(e.target.value)}/></label><button disabled={busy} onClick={() => void run(async () => { await api(`${docPath}/inspect`, "POST", {password}); setPassword(""); await refresh(doc.id, number); }, "Inspection complete.")}>Retry inspection / unlock</button></section>}
          {doc.status === "ready" && jobs.some(j => j.status === "paused" && j.message.startsWith("low_memory:")) && <section className="review-warning"><h3>Processing paused for available RAM</h3><MemoryStatus/></section>}
          {doc.metadata.form_review_required && <p className="review-warning">This PDF contains form fields. Compare their visible values with the original. Faithful form replacement requires separate qualification; companion exports preserve visible appearances.</p>}
          {!!doc.metadata.attachments?.length && <p className="review-warning">Embedded attachments are not translated: {doc.metadata.attachments.join(", ")}. Import them separately if needed.</p>}
          <details className="context-panel"><summary>Translation context and terminology</summary><div className="context-grid">
            <label>Translate into<select value={target} onChange={e => setTarget(e.target.value)}><option value="hi">Hindi</option><option value="en">English</option></select></label>
            <label>Subject / domain<input value={domain} onChange={e => setDomain(e.target.value)}/></label><label>Jurisdiction, if relevant<input value={jurisdiction} onChange={e => setJurisdiction(e.target.value)}/></label>
            <button disabled={busy || dirty} onClick={() => void run(async () => { await api(`${docPath}/context`, "PATCH", {revision: doc.revision, reviewer, target, domain, jurisdiction}); await refresh(doc.id, number); }, "Context saved. Affected approvals were cleared for fresh review.")}>Save context</button>
          </div><p className="muted">Definitions guide review. A model suggestion still needs a bilingual review of the complete clause.</p>
            {glossary.map(g => <div className="glossary-entry" key={g.id}><strong>{g.source} → {g.target}</strong><span>{g.sense} · {g.reference}</span></div>)}
            <div className="context-grid">{(["source", "target", "sense", "reference"] as const).map(k => <label key={k}>{label(k)}<input value={term[k]} onChange={e => setTerm({...term, [k]: e.target.value})}/></label>)}<button disabled={busy || dirty || Object.values(term).some(v => !v.trim())} onClick={() => void run(async () => { await api(`${docPath}/glossary`, "POST", {...term, revision: doc.revision, reviewer, direction: doc.target === "hi" ? "en-hi" : "hi-en", scope: "document_definition"}); setTerm({source: "", target: "", sense: "", reference: ""}); await refresh(doc.id, number); }, "Terminology recorded; affected approvals require review.")}>Add approved definition</button></div>
          </details>
          {!!doc.page_count && <>
            <div className="page-toolbar"><button disabled={busy || dirty || number <= 1} onClick={() => void run(() => navigate(number - 1))}>← Previous</button>
              <label>Page <input type="number" min={1} max={doc.page_count} value={number} disabled={busy || dirty} onChange={e => { const n = Number(e.target.value); if (n >= 1 && n <= doc.page_count) void run(() => navigate(n)); }}/> of {doc.page_count}</label>
              <button disabled={busy || dirty || number >= doc.page_count} onClick={() => void run(() => navigate(number + 1))}>Next →</button>
              <button disabled={busy || dirty || doc.status !== "ready"} onClick={() => void run(() => startJob("extract"), "Extraction queued. Progress is saved after each page.")}>Extract page</button>
              <button disabled={busy || dirty || doc.status !== "ready"} onClick={() => void run(() => startJob("extract", true), "Document extraction queued. You can pause and resume it.")}>Extract remaining pages</button>
            </div>
            {jobs.slice(0, 2).map(j => <div className="job-row" key={j.id}><span>{label(j.kind)} · {label(j.status)} · {j.cursor}/{j.payload.pages.length}</span><small>{j.message}</small>
              {!["completed", "cancelled"].includes(j.status) && <div>{(j.status === "paused" ? ["resume", "cancel"] : ["pause", "cancel"]).map(action => <button key={action} disabled={busy} onClick={() => void run(async () => { await api(`/jobs/${j.id}/${action}`, "POST"); await refresh(doc.id, number, !dirty); })}>{label(action)}</button>)}</div>}</div>)}
            {page && <div className="review-columns">
              <section className="source-panel"><div className="panel-heading"><div><span>01 / ORIGINAL</span><h3>Account for the whole page</h3></div><button disabled={busy || dirty || imageError} className={drawing ? "active" : ""} onClick={() => setDrawing(!drawing)}>{drawing ? "Cancel drawing" : "+ Draw missing region"}</button></div>
                <p className="muted">Click a region to review it. Check margins, faint text, stamps and footnotes beyond the detected boxes.</p>
                {imageError ? <div className="review-warning">Preview could not be rendered. Check available memory or unlock the PDF. <button onClick={() => setImageError(false)}>Retry preview</button></div> : <div className="source-canvas" style={{aspectRatio: `${page.width}/${page.height}`}}>
                  <img src={reviewUrl(`${pagePath}/image`)} alt={`Original PDF page ${number}`} onError={() => setImageError(true)}/>
                  <svg viewBox={`0 0 ${page.width} ${page.height}`} className={drawing ? "drawing" : ""} onPointerDown={e => { if (drawing) { e.currentTarget.setPointerCapture(e.pointerId); setAnchor(point(e)); setCorner(point(e)); } }} onPointerMove={e => { if (anchor) setCorner(point(e)); }} onPointerUp={e => void finishDraw(e)}>
                    {page.regions.map(r => <g key={r.id} onClick={() => { if (!drawing && !dirtyRef.current && !busy && !noteStorageBlocked) setRegion(r); }} className={`region-box ${region?.id === r.id ? "selected" : ""} ${r.source_approved ? "approved" : ""}`}><rect x={r.bbox[0]} y={r.bbox[1]} width={r.bbox[2] - r.bbox[0]} height={r.bbox[3] - r.bbox[1]}/><text x={r.bbox[0] + 3} y={r.bbox[1] + 11}>{r.order + 1}</text></g>)}
                    {anchor && corner && <rect className="drawing-box" x={Math.min(anchor[0], corner[0])} y={Math.min(anchor[1], corner[1])} width={Math.abs(corner[0] - anchor[0])} height={Math.abs(corner[1] - anchor[1])}/>}
                  </svg>
                </div>}
                {(!!page.table_proposals?.warnings.length || page.table_proposals?.status === "failed") && <div className="review-warning"><p>The table detector reported limitations. Check all visible grids against the original and record how their content was accounted for.</p>{page.table_proposals.warnings.map((w, i) => <p key={i}>{w.message}</p>)}<label>Structure warning review note<textarea aria-label="Structure warning review note" disabled={busy} value={structureNote} onChange={e => coverageNotes.edit({note: e.target.value})}/></label></div>}
                {coverageNotes.dirty && <p className="review-warning">{coverageNotes.storageFailed ? "Browser storage could not save these review notes. Keep this page open until you approve or discard them." : coverageNotes.stale ? "Recovered completeness notes from an earlier page revision. Check them against the current original before approving." : "Completeness notes are saved in this browser until approval."} <button disabled={busy} onClick={coverageNotes.clear}>Discard completeness notes</button></p>}
                <button className="primary" disabled={busy || dirty || page.status !== "extracted" || imageError} onClick={() => void run(() => approve("coverage"), "Whole-page completeness review recorded.")}>{page.coverage_approved ? "✓ Completeness reviewed" : "I checked the entire page for missing content"}</button>
                {page.status !== "extracted" && <details><summary>Complete this page manually</summary><p>Draw regions, transcribe the visible content, and record the reason automatic processing could not be used.</p><label>Manual transcription note<input value={manualReason} onChange={e => setManualReason(e.target.value)}/></label><button disabled={busy || dirty || !page.regions.length || manualReason.trim().length < 10} onClick={() => void run(async () => { await api(`${pagePath}/manual`, "POST", {revision: page.revision, reviewer, reason: manualReason}); await refresh(doc.id, number); }, "Manual transcription recorded. Content and completeness still require approval.")}>Record manual page transcription</button></details>}
              </section>
              <section className="region-panel"><div className="panel-heading"><div><span>02 / CONTENT & MEANING</span><h3>Review the selected region</h3></div><span>{dirty ? "Unsaved changes" : `Page revision ${page.revision}`}</span></div>
                <label>Region<select aria-label="Region" value={region?.id ?? ""} disabled={busy || dirty || noteStorageBlocked} onChange={e => setRegion(page.regions.find(r => r.id === e.target.value))}><option value="">Select a region</option>{page.regions.map(r => <option key={r.id} value={r.id}>{r.order + 1}. {r.kind} · {label(r.disposition)}</option>)}</select></label>
                {region && <div className="reading-order-controls"><p className="muted">Reading order: {page.regions.findIndex(r => r.id === region.id) + 1} of {page.regions.length}. Put headings, columns and notes in the sequence they should be read. A change clears translation and completeness approvals for this document.</p>
                  <div className="button-row"><button disabled={busy || dirty || page.regions[0]?.id === region.id} onClick={() => void run(() => moveRegion(-1), "Reading order saved. Translation and completeness approvals need fresh review.")}>↑ Move earlier</button>
                  <button disabled={busy || dirty || page.regions[page.regions.length - 1]?.id === region.id} onClick={() => void run(() => moveRegion(1), "Reading order saved. Translation and completeness approvals need fresh review.")}>↓ Move later</button>
                </div></div>}
                {!region ? <p className="muted">Extract the page to create region proposals. Then compare each one with the original.</p> : <>
                  <fieldset className="region-edit-fields" disabled={busy}>
                  <div className="context-grid"><label>Content type<select aria-label="Content type" value={region.kind} onChange={e => editRegion({kind: e.target.value})}>{["paragraph", "heading", "list", "footnote", "caption", "formula", "image", "manual", "page", "table_cell"].map(k => <option key={k}>{k}</option>)}</select></label>
                    <label>What should happen?<select aria-label="Region disposition" value={region.disposition} onChange={e => editRegion({disposition: e.target.value})}>{["translate", "already_target", "preserve", "decorative", "unreadable", "excluded"].map(k => <option key={k} value={k}>{label(k)}</option>)}</select></label>
                    <label>Confirm source language<select aria-label="Confirm source language" value={region.source_language} onChange={e => editRegion({source_language: e.target.value})}><option value="uncertain">Uncertain</option><option value="en">English</option><option value="hi">Hindi</option><option value="mixed">Mixed / needs splitting</option></select></label>
                  </div>
                  <label className="checkbox-label"><input type="checkbox" checked={region.contains_text} onChange={e => editRegion({contains_text: e.target.checked})}/>This region contains text</label>
                  {region.table_id && <p className="muted">Table cell: row {(region.row ?? 0) + 1}, column {(region.column ?? 0) + 1}; spans {region.row_span} rows and {region.column_span} columns. Compare the value with its headers below.</p>}
                  {region.method === "form-field" && <div className="review-warning"><h4>Reconcile this form field</h4>
                    <p>Stored and displayed values can disagree. Check the original, choose the verified source value, and explain the choice.</p>
                    {region.form_evidence_changed && <p>The evidence changed. Your previous transcription and reconciliation note are preserved; compare them with the refreshed evidence before approving again.</p>}
                    {region.form_evidence && <><p>Comparison: {label(region.form_evidence.status)}</p><label>Stored value<pre>{String(region.form_evidence.stored_value ?? "Empty or unavailable")}</pre></label>
                      <label>Text from the existing appearance<pre>{region.form_evidence.appearance?.text || "No reliable appearance text; inspect the original visually."}</pre></label></>}
                    <label>Form reconciliation note<textarea aria-label="Form reconciliation note" rows={3} value={region.form_review_note ?? ""} onChange={e => editRegion({form_review_note: e.target.value})}/></label>
                  </div>}
                  <details><summary>Raw extraction and recognition alternatives</summary><pre>{region.raw || "No native text was extracted."}</pre>{region.evidence.map((v, i) => <p key={i}>{v}</p>)}{region.alternatives.map((a, i) => <div key={i}><strong>{a.method}</strong><p>{a.error || a.warning}</p><pre>{a.text}</pre>{a.text && <button onClick={() => editRegion({source: a.text, disposition: "translate"})}>Use as an editable transcription</button>}</div>)}</details>
                  <label>Verified source<textarea aria-label="Verified source" value={region.source} onChange={e => editRegion({source: e.target.value})} spellCheck={false} rows={7}/></label>
                  <label>Translation<textarea aria-label="Translation" value={region.translation} onChange={e => editRegion({translation: e.target.value})} spellCheck={false} rows={7}/></label>
                  <label>Reason / context note<input aria-label="Reason / context note" value={region.reason} onChange={e => editRegion({reason: e.target.value})}/></label>
                  <div className="button-row"><button className="primary" disabled={busy || !dirty} onClick={() => void run(saveRegion)}>Save changes</button><button disabled={busy || !dirty} onClick={() => { try { localStorage.removeItem(draftKey(region.id)); } catch {} ++editGeneration.current; setRegion(page.regions.find(r => r.id === region.id)); setDirty(false); dirtyRef.current = false; }}>Discard edits</button></div>
                  </fieldset>
                  <div className="review-approval"><button disabled={busy || dirty} onClick={() => void run(() => approve("source"), "Source recognition reviewed.")}>{region.source_approved ? "✓ Source reviewed" : "Approve source recognition"}</button>
                    <button disabled={busy || dirty || !region.source_approved || !!unavailableDirections.length} onClick={() => void run(() => startJob("translate"), "Translation job queued.")}>Generate page translations</button></div>
                  {!!unavailableDirections.length && <div className="review-warning" aria-label="Local translation availability">{unavailableDirections.map(direction => <p key={direction}>{direction === "en-hi" ? "English to Hindi" : "Hindi to English"}: {caps!.translation[direction].reason}</p>)}<p>You can enter and review translations manually.</p></div>}
                  {requiredDirections.some(direction => caps?.translation[direction]?.draft_only) && <p className="muted">The installed model produces draft suggestions. Check the complete meaning, terminology, names and numbers before approving each translation.</p>}
                  {!!region.checks?.length && <div className="meaning-checks"><h4>Meaning checks requiring a specific review note</h4>{region.checks.map(c => <label key={c.code}>{c.message}<textarea disabled={busy} rows={2} value={ack[c.code] ?? ""} onChange={e => meaningNotes.edit({...ack, [c.code]: e.target.value})} placeholder="What did you verify or correct?"/></label>)}</div>}
                  {meaningNotes.dirty && <p className="review-warning">{meaningNotes.storageFailed ? "Browser storage could not save these review notes. Keep this page open until you approve or discard them." : meaningNotes.stale ? "Recovered meaning notes from an earlier page revision. Check them against the current content before approving." : "Meaning notes are saved in this browser until approval."} <button disabled={busy} onClick={meaningNotes.clear}>Discard meaning notes</button></p>}
                  <button className="primary" disabled={busy || dirty || !region.source_approved || region.disposition !== "translate"} onClick={() => void run(() => approve("translation"), "Translation and meaning checks reviewed.")}>{region.translation_approved ? "✓ Translation reviewed" : "Approve translation and meaning"}</button>
                  <details><summary>Optional cloud assistance for this region</summary><p>{caps?.cloud.reason}</p><button disabled={busy || dirty || !region.source_approved} onClick={() => void run(async () => { setCloudRequest(await api(`${docPath}/cloud-requests`, "POST", {revision: doc.revision, reviewer, page: number, region_ids: [region.id]})); setCloudEligible(false); }, "Payload prepared locally. No document content has been sent.")}>Preview exact cloud payload</button>
                    {cloudRequest && <div><p>{cloudRequest.notice}</p><pre>{JSON.stringify(cloudRequest.payload, null, 2)}</pre><label className="checkbox-label"><input type="checkbox" checked={cloudEligible} onChange={e => setCloudEligible(e.target.checked)}/>I confirm every part of this payload is public, nonpersonal and nonconfidential.</label><button disabled={busy || dirty || !cloudEligible || !caps?.cloud.available} onClick={() => void run(async () => { await api(`/cloud-requests/${cloudRequest.id}/approve`, "POST", {payload_sha256: cloudRequest.payload_sha256, public_nonpersonal: true, reviewer}); setCloudRequest(undefined); await refresh(doc.id, number); }, "Cloud suggestion saved without approval. Review its complete meaning against the source.")}>Approve this one cloud job</button></div>}
                  </details>
                </>}
              </section>
            </div>}
            {page && <TableReviewPanel key={`${doc.id}:${number}`} page={page} disabled={busy || dirty || noteStorageBlocked}
              select={id => setRegion(page.regions.find(r => r.id === id))}
              review={(id, action, note) => void run(async () => {
                const p = await api<ReviewPage>(`${pagePath}/tables/${id}/review`, "POST", {revision: page.revision, reviewer, action, note});
                setPage(p); setRegion(p.regions.find(r => r.id === region?.id) ?? p.regions[0]); await refresh(doc.id, number, false);
              }, "Table decision saved. Review cell content and whole-page completeness.")}/>}
            {page?.status === "extracted" && caps && !caps.structure.manual_tables && <p className="review-warning" role="status">
              Manual table editing needs the updated local server. Save your work, restart PDF Reader with Start-Local.ps1, then refresh this page.
            </p>}
            {page?.status === "extracted" && caps?.structure.manual_tables && <ManualTableEditor key={`grid:${doc.id}:${number}`} page={page}
              draftKey={`pdf-review-grid-draft:${doc.id}:${number}`} onDirtyChange={setGridDirty}
              previewUrl={reviewUrl(`${pagePath}/image`)} disabled={busy || dirty}
              onCreate={async body => {
                beginMutation();
                try {
                  const p = await api<ReviewPage>(`${pagePath}/tables/manual`, "POST", {...body, reviewer});
                  setPage(p);
                  if (!dirtyRef.current) setRegion(p.regions.find(r => r.id === region?.id) ?? p.regions.find(r => r.method === "manual-table") ?? p.regions[0]);
                  await refresh(doc.id, number, false);
                  setMessage("Manual grid saved. Original extraction is kept under Table relationships. Transcribe or confirm every cell, then review structure and completeness.");
                } finally { finishMutation(); }
              }}/>}
            {page?.status === "extracted" && <FormEvidenceReview key={`${doc.id}:${number}`} page={page} busy={busy || dirty}
              onPreview={async () => {
                beginMutation();
                try { return await api<FormEvidencePreview>(`${pagePath}/form-evidence/preview`, "POST", {
                  revision: page.revision, document_revision: doc.revision, reviewer}); }
                finally { finishMutation(); }
              }}
              onApply={async (preview, bindings, note) => {
                beginMutation();
                try {
                  const p = await api<ReviewPage>(`${pagePath}/form-evidence/apply`, "POST", {
                    revision: preview.page_revision, document_revision: preview.document_revision, reviewer,
                    preview_id: preview.id, bindings, note});
                  setPage(p);
                  if (!dirtyRef.current) setRegion(p.regions.find(r => r.id === region?.id) ?? p.regions[0]);
                  await refresh(doc.id, number, false);
                  setMessage("Form evidence refreshed. Your saved text is preserved; review changed fields and page completeness.");
                } finally { finishMutation(); }
              }}/>}
            {!!page?.blockers.length && <details className="issues-panel"><summary>{page.blockers.length} checks remaining on this page</summary><ul>{page.blockers.map((b, i) => <li key={i}>{b}</li>)}</ul></details>}
          </>}
          <section className="export-panel"><div className="panel-heading"><div><span>03 / PRESENTATION</span><h3>Create and review an export</h3></div></div><p>Exports are fixed snapshots. Editing the document makes earlier snapshots ineligible for final approval. Word and text downloads remain review copies.</p>
            <div className="button-row"><label>Format<select value={format} onChange={e => setFormat(e.target.value)}><option value="pdf">PDF</option><option value="docx">Word</option><option value="txt">Text</option></select></label><label>Layout<select value={mode} disabled={format !== "pdf"} onChange={e => setMode(e.target.value)}><option value="readable">Readable translation</option><option value="faithful">Preserve digital page layout</option><option value="companion">Original + companion translation</option></select></label>
              <button className="primary" disabled={busy || dirty || !doc.page_count} onClick={() => void run(async () => { const e = await api<ReviewExport>(`${docPath}/exports`, "POST", {revision: doc.revision, reviewer, format, mode: format === "pdf" ? mode : "readable"}); await refresh(doc.id, number, false); setArtifact(e); setExportPage(1); }, "Export snapshot created. Inspect its pages before final approval.")}>Create review copy</button></div>
            <div className="export-list">{exports.map(e => <button key={e.id} disabled={busy} className={e.id === artifact?.id ? "active" : ""} onClick={() => { setArtifact(e); setExportPage(1); }}>{e.format.toUpperCase()} · r{e.revision} · {label(e.status)}{!e.current && " · older revision"}</button>)}</div>
            {artifact && <div><div className="button-row"><a href={reviewUrl(`/exports/${artifact.id}/download`)}>Download review copy</a><a href={reviewUrl(`/exports/${artifact.id}/report`)} target="_blank" rel="noreferrer">Validation report</a>{artifact.status === "reviewed_complete" && <a className="primary download-final" href={reviewUrl(`/exports/${artifact.id}/download?final=true`)}>Download reviewed PDF</a>}</div>
              {!!artifact.blockers.length && <div className="review-warning"><p>This review copy has {artifact.blockers.length} unresolved issues. Final approval stays blocked until these issues are resolved in a new export.</p><ul>{artifact.blockers.map((issue, index) => <li key={index}>{issue}</li>)}</ul></div>}
              {artifact.page_count > 0 && <><div className="page-toolbar"><button disabled={busy || exportPage <= 1} onClick={() => setExportPage(exportPage - 1)}>←</button><span>Export page {exportPage} / {artifact.page_count}</span><button disabled={busy || exportPage >= artifact.page_count} onClick={() => setExportPage(exportPage + 1)}>→</button><button disabled={busy || !artifact.current || !!artifact.blockers.length || !exportImageReady || exportImageError} onClick={() => void run(async () => { const e = await api<ReviewExport>(`/exports/${artifact.id}/approvals`, "POST", {page: exportPage, reviewer, confirmed: true}); setArtifact(e); setExports(values => values.map(x => x.id === e.id ? e : x)); }, "Export page layout reviewed.")}>{artifact.layout_approvals[String(exportPage)] ? "✓ Page layout reviewed" : "I checked text, glyphs, fit and layout on this page"}</button></div>
                {exportImageError ? <div className="review-warning">Export preview could not be rendered. Check available memory and retry. <button onClick={() => { setExportImageError(false); setExportImageReady(false); }}>Retry export preview</button></div> : <><p className="muted" role="status">{exportImageReady ? "Compare this rendered page with the approved content before reviewing its layout." : "Loading export preview…"}</p><img className="export-preview" src={reviewUrl(`/exports/${artifact.id}/pages/${exportPage}/image`)} alt={`Export review page ${exportPage}`} onLoad={() => setExportImageReady(true)} onError={() => { setExportImageError(true); setExportImageReady(false); }}/></>}</>}
            </div>}
          </section>
        </>}
      </section>
    </main><footer className="review-footer">Your original remains unchanged. Automated suggestions require review. Unknown or unreadable content keeps an export partial.</footer>
  </div>;
}
