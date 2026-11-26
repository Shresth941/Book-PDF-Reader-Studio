import {PublishedEnglish, GoogleComparison} from "./ConstitutionReference";
import React, {useEffect, useRef, useState} from "react";
import {reviewCall as api, reviewUrl, ReviewDocument, ReviewPage, ReviewJob, ReviewExport, Region, Capabilities} from "./reviewApi";
import {ReviewWorkspace} from "./ReviewWorkspace";
import {MemoryStatus} from "./MemoryStatus";
import "./reader.css";
import {PageTextEditor} from "./PageTextEditor";

type Edits = Record<string, {source: string; translation: string}>;
const languages = {en: "English", hi: "Hindi"};
const textRegion = (r: Region) => ["translate", "already_target"].includes(r.disposition) && r.method !== "form-field" && !r.table_id;
const recognizedText = (r: Region) => r.disposition === "unreadable" && r.method !== "form-field" && !r.table_id && !r.overlaps?.length
  ? r.alternatives.find(a => a.text.trim() && !a.error)?.text : undefined;
const decodedSuggestion = (r: Region) => r.alternatives.find(a => a.method === "xpress-chanak-v1" && a.text !== r.source)?.text;
const draftKey = (id: string, number: number) => `pdf-reader-page:${id}:${number}`;

// Keep the specialist workspace on the same records; switching never copies a document.
export function ReaderWorkspace() {
  const [advanced, setAdvanced] = useState(() => {try {return sessionStorage.getItem("pdf-reader-detailed") === "true";} catch {return false;}});
  const [selected, setSelected] = useState<string>();
  function switchMode(id: string | undefined, detailed: boolean) {
    try {sessionStorage.setItem("pdf-reader-detailed", String(detailed));} catch { /* Switching still works when browser storage is blocked. */ }
    setSelected(id); setAdvanced(detailed);
  }
  return advanced ? <ReviewWorkspace initialDocumentId={selected} onExit={id => switchMode(id, false)}/> :
    <SimpleReader initialDocumentId={selected} onAdvanced={id => switchMode(id, true)}/>;
}

function SimpleReader({initialDocumentId, onAdvanced}: {initialDocumentId?: string; onAdvanced: (id?: string) => void}) {
  const [library, setLibrary] = useState<ReviewDocument[]>([]);
  const [doc, setDoc] = useState<ReviewDocument>();
  const [page, setPage] = useState<ReviewPage>();
  const [number, setNumber] = useState(1);
  const [jobs, setJobs] = useState<ReviewJob[]>([]);
  const [caps, setCaps] = useState<Capabilities>();
  const [edits, setEdits] = useState<Edits>({});
  const editsRef = useRef(edits); editsRef.current = edits;
  const [storageFailed, setStorageFailed] = useState(false);
  const [busy, setBusy] = useState("");
  const lock = useRef(false);
  const active = useRef(0);
  const [message, setMessage] = useState("Your original PDF stays unchanged. Everything is saved on this computer.");
  const [error, setError] = useState(false);
  const [source, setSource] = useState<"en" | "hi">("en");
  const [target, setTarget] = useState<"en" | "hi">("hi");
  const [translationStyle, setTranslationStyle] = useState("general");
  const languagePage = useRef("");
  const [checked, setChecked] = useState(false);
  const [tab, setTab] = useState<"source" | "translation" | "compare" | "published">("source");
  const [showOriginal, setShowOriginal] = useState(true);
  const [imageError, setImageError] = useState(false);
  const [imageAttempt, setImageAttempt] = useState(0);
  const [password, setPassword] = useState("");
  const [format, setFormat] = useState("pdf");
  const [layout, setLayout] = useState("readable");
  const [artifact, setArtifact] = useState<ReviewExport>();
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [zoom, setZoom] = useState(100);
  const dirty = Object.keys(edits).length > 0;
  const running = jobs.some(j => ["queued", "running"].includes(j.status));
  const blocked = !!busy || running;
  const base = doc ? `/documents/${doc.id}` : "";
  const pagePath = `${base}/pages/${number}`;

  async function run(title: string, action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(title); setError(false);
    try { await action(); }
    catch (e) { setError(true); setMessage(e instanceof Error ? e.message : "Something went wrong. Please try again."); }
    finally { lock.current = false; setBusy(""); }
  }
  async function loadLibrary() {
    const result = await api<{documents: ReviewDocument[]}>("/documents");
    setLibrary(result.documents); return result.documents;
  }
  async function refresh(id: string, n: number, epoch = active.current) {
    const d = await api<ReviewDocument>(`/documents/${id}`);
    const [j, p] = await Promise.all([
      api<{jobs: ReviewJob[]}>(`/documents/${id}/jobs`),
      d.page_count ? api<ReviewPage>(`/documents/${id}/pages/${n}`) : Promise.resolve(undefined),
    ]);
    if (epoch !== active.current) return;
    if (jobs.some(j => ["queued", "running"].includes(j.status)) && !j.jobs.some(j => ["queued", "running"].includes(j.status))) {
      const paused = j.jobs.find(j => j.status === "paused");
      setError(!!paused);
      setMessage(paused ? "Processing paused. Read the message below, then choose Resume when you’re ready." : "Your page is ready. You can read, edit, translate, or download it.");
    }
    setDoc(d); setJobs(j.jobs); setPage(p);
    if (languagePage.current === "" && p?.english_reference) setTranslationStyle("legal");
    const languageKey = `${id}:${n}`;
    if (p?.status === "extracted" && languagePage.current !== languageKey) {
      const hints = p.regions.filter(r => textRegion(r) && /[A-Za-z\u0900-\u097f]/.test(r.source)).map(r =>
        /[\u0900-\u097f]/.test(r.source) && !/[A-Za-z]/.test(r.source) ? "devanagari" : "other");
      if (hints.length) {
        languagePage.current = languageKey;
        if (hints.filter(h => h === "devanagari").length > hints.length / 2) {setSource("hi"); setTarget("en"); setChecked(false);}
      }
    }
    setLibrary(values => values.map(item => item.id === d.id ? d : item));
  }
  function recover(id: string, n: number) {
    let restored: Edits = {};
    try {
      const raw = JSON.parse(localStorage.getItem(draftKey(id, n)) || "{}");
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        for (const [key, value] of Object.entries(raw)) {
          const v = value as {source?: unknown; translation?: unknown};
          if (v && typeof v.source === "string" && typeof v.translation === "string") restored[key] = {source: v.source, translation: v.translation};
        }
      }
      setStorageFailed(false);
    } catch { setStorageFailed(true); }
    editsRef.current = restored; setEdits(restored);
    if (Object.keys(restored).length) setMessage("Recovered your unsaved changes. Compare them with the original, then save.");
  }
  async function openDocument(d: ReviewDocument, n = 1) {
    if (Object.keys(editsRef.current).length) {setMessage("Save or discard your changes before leaving this page."); return;}
    const epoch = ++active.current;
    languagePage.current = "";
    setTranslationStyle(/legal|law|court|constitution|विधि/i.test(d.domain) || /\b(coi|law|act|constitution|easements?)\b|संविधान|अधिनियम/i.test(d.filename) ? "legal" : "general");
    setDoc(d); setNumber(n); setPage(undefined); setJobs([]); setArtifact(undefined);
    setChecked(false); setImageError(false); setPassword(""); setTarget(d.target); setSource(d.target === "hi" ? "en" : "hi");
    setTab(previous => d.id === doc?.id && previous === "published" ? "published" : "source"); setLibraryOpen(false);
    await refresh(d.id, n, epoch); recover(d.id, n);
  }
  useEffect(() => {
    void run("Opening your workspace…", async () => {
      const documents = await loadLibrary(); setCaps(await api<Capabilities>("/capabilities"));
      setLibraryOpen(documents.length > 0 && !initialDocumentId);
      const selected = documents.find(d => d.id === initialDocumentId);
      if (selected) await openDocument(selected);
    });
    return () => { ++active.current; };
  }, []);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {if (Object.keys(editsRef.current).length) {e.preventDefault(); e.returnValue = "";}};
    window.addEventListener("beforeunload", guard); return () => window.removeEventListener("beforeunload", guard);
  }, []);
  useEffect(() => {
    if (!doc || !running) return;
    let stopped = false, polling = false;
    const epoch = active.current;
    const timer = window.setInterval(async () => {
      if (stopped || polling || lock.current) return;
      polling = true;
      try {await refresh(doc.id, number, epoch);}
      catch (e) {if (!stopped) {setError(true); setMessage(e instanceof Error ? e.message : "Could not refresh progress. Try reopening this document.");}}
      finally {polling = false;}
    }, 1500);
    return () => {stopped = true; window.clearInterval(timer);};
  }, [doc?.id, number, running]);

  function storeEdits(next: Edits) {
    editsRef.current = next; setEdits(next); setChecked(false); setArtifact(undefined);
    try {
      if (Object.keys(next).length) localStorage.setItem(draftKey(doc!.id, number), JSON.stringify(next));
      else localStorage.removeItem(draftKey(doc!.id, number));
      setStorageFailed(false);
    } catch {setStorageFailed(true);}
  }
  async function save() {
    if (!doc || !page) return;
    let current = page;
    for (const [id, edit] of Object.entries(editsRef.current)) {
      const r = current.regions.find(r => r.id === id);
      if (!r || !textRegion(r)) throw new Error("The page structure changed. Your draft is retained. Open Detailed review to compare it before saving.");
      current = await api<ReviewPage>(`${pagePath}/regions/${id}`, "PATCH", {
        revision: current.revision, source: edit.source, translation: edit.translation,
        source_language: r.source_language, disposition: r.disposition, contains_text: r.contains_text, reason: r.reason, kind: r.kind,
      });
      setPage(current);
      const next = {...editsRef.current}; delete next[id]; storeEdits(next);
    }
    await refresh(doc.id, number); setMessage("Changes saved. Your original PDF is unchanged.");
  }
  async function extract(all = false) {
    await api(`${base}/jobs`, "POST", {kind: "extract", start: all ? 1 : number, end: all ? doc!.page_count : number});
    setMessage(all ? "Reading the document. You can pause at any time; finished pages are saved." : "Reading this page. The extracted text will appear here.");
    await refresh(doc!.id, number);
  }
  async function upload(file: File) {
    if (!file.name.toLowerCase().endsWith(".pdf")) throw new Error("Choose a PDF file ending in .pdf.");
    const form = new FormData(); form.append("file", file);
    const d = await api<ReviewDocument>("/documents", "POST", form);
    await loadLibrary(); await openDocument(d);
    if (d.status === "ready" && d.page_count) {
      await api(`/documents/${d.id}/jobs`, "POST", {kind: "extract", start: 1, end: 1});
      await refresh(d.id, 1); setMessage("PDF opened. We’re reading the first page for you.");
    } else setMessage("Your original is saved. Follow the instructions below to open it.");
  }
  function downloadPageText() {
    if (!page || !doc) return;
    const text = page.regions.map(r => r.source || "[No text extracted from this section]").join("\n\n");
    const url = URL.createObjectURL(new Blob([`Extracted text — review against the original\n${doc.filename} — page ${number}\n\n${text}`], {type: "text/plain;charset=utf-8"}));
    const link = document.createElement("a"); link.href = url; link.download = `extracted-page-${number}.txt`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function adoptRecognition(r: Region) {
    const text = recognizedText(r) || decodedSuggestion(r);
    if (!page || !doc || !text) return;
    await api<ReviewPage>(`${pagePath}/regions/${r.id}`, "PATCH", {
      revision: page.revision, source: text, translation: r.translation, source_language: "uncertain",
      disposition: "translate", contains_text: true, reason: r.reason, kind: r.kind,
    });
    setChecked(false); setArtifact(undefined); await refresh(doc.id, number);
    setMessage("Recognized text is ready to edit. Compare it with the original and correct mistakes before translating.");
  }
  async function translate() {
    if (!checked || dirty || !doc || !page || source === target) return;
    let current = await api<ReviewPage>(pagePath);
    if (current.revision !== page.revision) throw new Error("This page changed since you checked it. Reopen the document and check the latest text before translating.");
    const d = await api<ReviewDocument>(base);
    const legalDomain = /legal|law|court|constitution|विधि/i.test(d.domain);
    const domain = translationStyle === "legal" ? (legalDomain ? d.domain : "legal") : (legalDomain ? "general" : d.domain);
    if (d.target !== target || d.domain !== domain) {
      await api(`${base}/context`, "PATCH", {revision: d.revision, target, domain, jurisdiction: d.jurisdiction});
      current = await api<ReviewPage>(pagePath);
    }
    for (const r of current.regions.filter(textRegion)) {
      if (!r.source.trim()) throw new Error("Some text is empty. Fill it in or use Detailed review to mark a blank area.");
      if (r.source_language !== source || r.disposition !== "translate") {
        current = await api<ReviewPage>(`${pagePath}/regions/${r.id}`, "PATCH", {
          revision: current.revision, source: r.source, translation: r.translation,
          source_language: source, disposition: "translate", contains_text: true, reason: r.reason, kind: r.kind,
        });
      }
      const updated = current.regions.find(x => x.id === r.id)!;
      if (!updated.source_approved) current = await api<ReviewPage>(`${pagePath}/approvals`, "POST", {
        revision: current.revision, kind: "source", region_id: r.id,
      });
      setPage(current);
    }
    await api(`${base}/jobs`, "POST", {kind: "translate", start: number, end: number});
    setChecked(false); setTab("translation"); await refresh(doc.id, number);
    setMessage("Translating this page. The result is a draft: check names, numbers, and meaning.");
  }
  const regions = page?.regions ?? [];
  const needsSpecialist = regions.some(r => r.disposition === "translate" && !textRegion(r));
  const hasText = regions.some(r => textRegion(r) && r.source.trim());
  const hasTranslation = regions.some(r => !!r.translation.trim());
  const publishedMatches = regions.filter(r => textRegion(r) && r.published_match && !r.translation_approved
    && r.translation !== r.published_match.english);
  const termChecks = [...new Set(regions.flatMap(r => r.translation.trim() ? r.checks.filter(c => c.code.startsWith("glossary:")).map(c => c.message) : []))];
  const model = caps?.translation[`${source}-${target}`];
  const uploadButton = (label: string) => <label className={`reader-upload ${blocked || dirty ? "disabled" : ""}`}>{label}<input aria-label={label} type="file" accept="application/pdf,.pdf" disabled={blocked || dirty} onChange={e => {
    const file = e.target.files?.[0]; e.target.value = "";
    if (file) void run("Opening PDF…", () => upload(file));
  }}/></label>;

  return <div className="reader-app">
    <header className="reader-header"><a className="reader-brand" href="#" onClick={e => {e.preventDefault(); setLibraryOpen(v => !v);}}><span aria-hidden="true">▤</span><strong>PDF Reader Studio</strong></a>
      <div className="reader-actions"><span className="reader-private">Saved on this computer</span><button aria-expanded={libraryOpen} onClick={() => setLibraryOpen(v => !v)}>My PDFs</button><button disabled={blocked || dirty} onClick={() => onAdvanced(doc?.id)}>Detailed review</button>{uploadButton("Open PDF")}</div>
    </header>
    <div className={`reader-notice ${error ? "reader-error" : ""}`} role={error ? "alert" : "status"}>{busy || message}</div>
    {libraryOpen && <section className="reader-library" aria-label="My PDFs"><div className="reader-section-title"><h2>My PDFs</h2><button onClick={() => setLibraryOpen(false)} aria-label="Close document list">Close</button></div>
      {!library.length ? <p>Open a PDF to get started. Your saved documents will appear here.</p> : <div className="reader-files">{library.map(d => <button key={d.id} disabled={blocked || dirty} aria-pressed={d.id === doc?.id} onClick={() => void run("Opening document…", () => openDocument(d))}><strong>{d.filename}</strong><small>{d.page_count ? `${d.page_count} pages · ${d.progress.extracted} read` : "Needs attention"}</small></button>)}</div>}
    </section>}
    {!doc ? <main className="reader-welcome"><div className="reader-book" aria-hidden="true"><span>Original</span><span>Translation</span></div><h1>Your PDF, easier to read.</h1><p>Open a document, read its text, and translate between English and Hindi. Keep the original beside you.</p>{uploadButton("Choose a PDF")}<ol className="reader-steps"><li><b>Open</b><span>The first page is read automatically.</span></li><li><b>Read & translate</b><span>Check the text and choose a language.</span></li><li><b>Download</b><span>Save a PDF, Word document, or text file.</span></li></ol></main> : <main className="reader-document">
      <section className="reader-document-header"><div><h1>{doc.filename}</h1><p>{doc.page_count ? `${doc.page_count} pages · ${doc.progress.extracted} pages read` : "Your PDF is saved. Opening needs attention."}</p></div><a href={reviewUrl(`${base}/original`)} target="_blank" rel="noreferrer">Download original</a></section>
      {doc.status !== "ready" && <section className="reader-warning"><h2>Let’s open your PDF</h2><p>{doc.metadata.message}</p>{doc.metadata.code === "low_memory" && <MemoryStatus/>}
        <label>Password, if the PDF is locked<input type="password" autoComplete="off" value={password} onChange={e => setPassword(e.target.value)}/></label><button disabled={blocked} onClick={() => void run("Opening PDF…", async () => {await api(`${base}/inspect`, "POST", {password}); setPassword(""); await refresh(doc.id, number); setMessage("PDF opened. Choose Read this page to extract its text.");})}>Try opening again</button></section>}
      {!!doc.page_count && <>
        <nav className="reader-toolbar" aria-label="Page controls"><div className="reader-actions"><button aria-label="Previous page" disabled={blocked || dirty || number <= 1} onClick={() => void run("Opening page…", () => openDocument(doc, number - 1))}>←</button><label>Page<select aria-label="Page" value={number} disabled={blocked || dirty} onChange={e => void run("Opening page…", () => openDocument(doc, Number(e.target.value)))}>{Array.from({length: doc.page_count}, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}</select></label><span>of {doc.page_count}</span><button aria-label="Next page" disabled={blocked || dirty || number >= doc.page_count} onClick={() => void run("Opening page…", () => openDocument(doc, number + 1))}>→</button></div>
          <div className="reader-actions"><button disabled={blocked || dirty || doc.status !== "ready" || page?.status === "extracted"} onClick={() => void run("Starting extraction…", () => extract())}>{page?.status === "extracted" ? "✓ Page text ready" : "Read this page"}</button><button disabled={blocked || dirty || doc.status !== "ready" || doc.progress.extracted === doc.page_count} onClick={() => void run("Starting extraction…", () => extract(true))}>Read all pages</button><button aria-pressed={showOriginal} onClick={() => setShowOriginal(v => !v)}>{showOriginal ? "Hide original" : "Show original"}</button></div>
        </nav>
        {jobs.filter(j => !["completed", "cancelled"].includes(j.status)).map(j => <section className="reader-job" key={j.id} aria-label="Processing progress"><div><strong>{j.kind === "extract" ? "Reading pages" : "Translating pages"}{j.status === "paused" ? " — paused" : "…"}</strong><span>{j.cursor} of {j.payload.pages.length} pages finished</span><progress aria-label="Pages finished" value={j.cursor} max={j.payload.pages.length}/>{j.message && <p>{j.message}</p>}</div><button disabled={!!busy || dirty} onClick={() => void run("Updating progress…", async () => {await api(`/jobs/${j.id}/${j.status === "paused" ? "resume" : "pause"}`, "POST"); await refresh(doc.id, number);})}>{j.status === "paused" ? "Resume" : "Pause"}</button></section>)}
        {page?.error && <p className="reader-warning">{page.error}</p>}
        {page?.status === "extracted" && regions.some(r => r.fonts?.some(f => /shiva|kruti|chanak|devlys/i.test(f)) && (r.text_layout?.version !== 3 || !r.alternatives.some(a => a.method === "xpress-chanak-v1"))) && <div className="reader-help"><p>This page uses an older Hindi font. Refresh to recover Hindi directly where supported. Your saved corrections are kept.</p><button disabled={blocked || dirty} onClick={() => void run("Reading Hindi text…", async () => {
          await api(`${pagePath}/recognition`, "POST", {revision: page.revision});
          await refresh(doc.id, number); setChecked(false); setArtifact(undefined);
          setMessage("Hindi text refreshed. Saved corrections are kept. Check the text against the original before translating.");
        })}>Refresh Hindi text recognition</button></div>}
        {caps?.ocr.installed === false && <details className="reader-help"><summary>Working with a scanned PDF?</summary><p>Scanned pages need OCR to turn pictures into text. Tesseract is not installed on this computer. Digital PDFs still work. Install Tesseract with Hindi and English language data, restart the app, and retry, or use Detailed review to transcribe the page.</p></details>}
        <div className="reader-view-controls">
          <div className="reader-tabs" role="group" aria-label="Text view">{(["source", "translation", "compare"] as const).map(t => <button key={t} aria-pressed={tab === t} onClick={() => setTab(t)}>{t === "source" ? "Extracted text" : t === "translation" ? "Translation" : "Compare text"}</button>)}{page?.english_reference && <button aria-pressed={tab === "published"} onClick={() => setTab("published")}>Published English</button>}</div>
          <label>Page zoom<select aria-label="Page zoom" value={zoom} onChange={e => setZoom(Number(e.target.value))}><option value={100}>Fit</option><option value={125}>125%</option><option value={150}>150%</option><option value={200}>200%</option></select></label>
        </div>
        {hasText && tab !== "published" && <p className="reader-page-instruction">Click text on the page to edit. Position and spacing follow the original.</p>}
        <div className={`reader-desk ${showOriginal ? "" : "reader-text-only"}`}>
          {showOriginal && <section className="reader-original"><div className="reader-pane-title"><h2>Original page</h2><span>Page {number}</span></div><div className="reader-paper">
            {imageError ? <div className="reader-placeholder"><p>We couldn’t show this page. Try again, or open the original PDF above.</p><button onClick={() => {setImageError(false); setImageAttempt(v => v + 1);}}>Retry page preview</button></div> : <img style={{width: `${zoom}%`}} src={reviewUrl(`${pagePath}/image?attempt=${imageAttempt}`)} alt={`Original PDF page ${number}`} onError={() => setImageError(true)}/>}
          </div></section>}
          <section className="reader-text"><div className="reader-pane-title"><h2>Page text</h2><span>{dirty ? "Unsaved changes" : "Saved"}</span></div>

            {tab === "translation" && target === "en" && translationStyle === "legal" && !!publishedMatches.length &&
              <div className="reader-save"><span>{publishedMatches.length} passages match the published English edition.</span>
                <button disabled={blocked || dirty} onClick={() => {
                  const changes: Edits = {};
                  for (const r of publishedMatches) changes[r.id] = {source: r.source, translation: r.published_match!.english};
                  storeEdits(changes);
                }}>Use published wording</button></div>}
            {tab === "published" && page?.english_reference && <PublishedEnglish location={page.english_reference}/>}
            {tab !== "published" && !hasText && <div className="reader-placeholder"><h3>{running ? "Reading your page…" : "Bring the text into view"}</h3><p>{running ? "You can watch the progress above. Your original is safe." : regions.some(r => recognizedText(r)) ? "We found text in this scanned page. Check the suggestion below, then use it as editable text." : page?.status === "extracted" ? "No editable paragraphs were found. This may be a scan, a blank page, or a form. Open Detailed review to inspect it." : "Choose Read this page to turn the PDF into editable text."}</p></div>}
            {tab !== "published" && hasText && page && <>

              <div className={`reader-page-editors ${tab === "compare" ? "reader-page-compare" : ""}`}>
                {(["source", "translation"] as const).filter(field => tab === "compare" || tab === field).map(field => <div key={field}>
                  {tab === "compare" && <h3>{field === "source" ? "Extracted text" : "Translation"}</h3>}
                  <PageTextEditor page={page} regions={regions.filter(textRegion)} field={field} edits={edits} disabled={blocked} zoom={zoom}
                    onChange={(r, key, value) => storeEdits({...editsRef.current, [r.id]: {source: editsRef.current[r.id]?.source ?? r.source, translation: editsRef.current[r.id]?.translation ?? r.translation, [key]: value}})}/>
                </div>)}
              </div>
              {tab === "translation" && !hasTranslation && <p className="reader-page-note">Translate this page below, or click where the original text appears to type your translation.</p>}
            </>}
            {regions.some(r => !textRegion(r) || decodedSuggestion(r)) && <div className="reader-passages reader-recognition">{regions.map((r, i) => !textRegion(r) ? <div className="reader-unresolved" key={r.id}>{recognizedText(r) ? <><strong>Recognized text from section {i + 1}</strong><pre>{recognizedText(r)}</pre><p>Compare this suggestion with the original. It may contain mistakes.</p><button disabled={blocked || dirty} onClick={() => void run("Using recognized text…", () => adoptRecognition(r))}>Use recognized text from section {i + 1}</button></> : <>Section {i + 1}: {r.table_id ? "Table cell" : r.method === "form-field" ? "Form field" : "Image or unresolved content"}. Use Detailed review to check this section.</>}</div> : decodedSuggestion(r) ? <details className="reader-help" key={r.id}><summary>Updated Hindi text available</summary><pre>{decodedSuggestion(r)}</pre><p>Your saved text is kept on the page. Use this version only after comparing it with the original.</p><button disabled={blocked || dirty} onClick={() => void run("Using decoded Hindi text…", () => adoptRecognition(r))}>Use updated Hindi text</button></details> : null)}</div>}
            {dirty && <div className="reader-save"><span>{storageFailed ? "Draft storage is unavailable. Keep this tab open and save your work." : "Changes are kept in this browser until you save."}</span><button className="reader-primary" disabled={blocked} onClick={() => void run("Saving changes…", save)}>Save changes</button><button disabled={blocked} onClick={() => {if (window.confirm("Discard your unsaved changes on this page?")) storeEdits({});}}>Discard changes</button></div>}
            {hasText && <div className="reader-save"><button disabled={blocked || dirty} onClick={downloadPageText}>Download this page’s extracted text</button></div>}
          </section>
        </div>
        <section className="reader-translate"><div><h2>Translate this page</h2><p>Check the extracted text against the original first. Fix any missing or incorrect words above.</p></div><div className="reader-actions"><label>Text is in<select aria-label="Source language" value={source} disabled={blocked} onChange={e => {setSource(e.target.value as "en" | "hi"); setChecked(false);}}><option value="en">English</option><option value="hi">Hindi</option></select></label><span aria-hidden="true">→</span><label>Translate to<select aria-label="Translation language" value={target} disabled={blocked} onChange={e => {setTarget(e.target.value as "en" | "hi"); setChecked(false); setArtifact(undefined);}}><option value="hi">Hindi</option><option value="en">English</option></select></label></div>
          <label>Translation style<select aria-label="Translation style" value={translationStyle} disabled={blocked} onChange={e => {setTranslationStyle(e.target.value); setChecked(false);}}><option value="general">General</option><option value="legal">Legal terminology</option></select></label>
          {translationStyle === "legal" && <p className="reader-caption">Uses legal headings and terminology, including your document glossary. The offline model still needs review for the meaning of complete clauses.</p>}
          {!!termChecks.length && <details className="reader-help"><summary>{termChecks.length} legal or glossary terms need checking</summary><ul>{termChecks.map(text => <li key={text}>{text}</li>)}</ul></details>}
          {page?.english_reference && <p>Read this book in the original English wording. <button className="reader-primary" type="button" onClick={() => {setTab("published"); document.querySelector(".reader-text")?.scrollIntoView({block: "start"});}}>Read published English</button> Editable translations use verified matches where available and local drafts elsewhere.</p>}
          <GoogleComparison key={pagePath} regions={regions} source={source} target={target}/>
          <details className="reader-help"><summary>Optional online legal translation</summary><p>Offline is the default. Online assistance requires a configured provider and your approval of the exact text before anything is sent.</p><p>{caps?.cloud.reason}</p><button disabled={blocked || dirty} onClick={() => onAdvanced(doc?.id)}>Open glossary and online review</button></details>
          <label className="reader-check"><input type="checkbox" checked={checked} disabled={blocked || dirty || !hasText || needsSpecialist || imageError} onChange={e => setChecked(e.target.checked)}/>I checked the extracted text against the original. All editable passages on this page are in {languages[source]}.</label>
          <button className="reader-primary" disabled={blocked || dirty || !checked || source === target || !model?.available || needsSpecialist} onClick={() => void run("Preparing translation…", translate)}>{hasTranslation ? "Translate page again" : "Translate page"}</button>
          {dirty && <p>Save your edits before translating.</p>}{source === target && <p>Choose a different language to translate into.</p>}
          {needsSpecialist && <p>This page includes tables or form fields. Use Detailed review to check their structure before translating.</p>}
          {source !== target && model && !model.available && <div className="reader-warning">Automatic translation is unavailable. You can type a translation in the Translation tab. <details><summary>Setup details</summary>{model.reason}</details></div>}
          <p className="reader-caption">For mixed-language pages, use Detailed review to choose each passage’s language. Translations are drafts, not certified translations.</p>
        </section>
      </>}
      <section className="reader-download"><div><h2>Download your work</h2><p>Downloads include the document’s saved pages. Unread or unchecked content is marked as incomplete.</p></div><div className="reader-actions"><label>File type<select aria-label="Download format" value={format} disabled={blocked} onChange={e => {setFormat(e.target.value); setArtifact(undefined);}}><option value="pdf">PDF</option><option value="docx">Word</option><option value="txt">Text</option></select></label>{format === "pdf" && <label>Page layout<select aria-label="Download layout" value={layout} disabled={blocked} onChange={e => {setLayout(e.target.value); setArtifact(undefined);}}><option value="readable">Clean, readable text</option><option value="companion">Original pages + translation</option></select></label>}<button className="reader-primary" disabled={blocked || dirty || !doc.progress.extracted} onClick={() => void run("Preparing your download…", async () => {
        const d = await api<ReviewDocument>(base);
        const result = await api<ReviewExport>(`${base}/exports`, "POST", {revision: d.revision, format, mode: format === "pdf" ? layout : "readable"});
        setDoc(d); setArtifact(result); setMessage("Your review copy is ready. Use the download link below.");
      })}>Prepare download</button></div>
      {artifact && <div className="reader-ready"><a className="reader-primary" href={reviewUrl(`/exports/${artifact.id}/download`)}>Download {artifact.format.toUpperCase()} review copy</a><p>This copy may contain unreviewed translations or missing content. Use Detailed review for completeness checks and final approval.</p></div>}
      <p className="reader-caption">Need precise layout, tables, form fields, or a fully reviewed PDF? <button disabled={blocked || dirty} onClick={() => onAdvanced(doc.id)}>Open detailed review</button></p></section>
    </main>}
    <footer className="reader-footer">Originals stay unchanged. Save edits before closing. <button disabled={blocked || dirty} onClick={() => void run("Preparing backup…", async () => {const b = await api<{id: string}>("/backups", "POST"); window.location.assign(reviewUrl(`/backups/${b.id}`));})}>Back up my PDFs</button></footer>
  </div>;
}
