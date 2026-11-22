import React, {useEffect, useRef, useState} from "react";
import {ReviewPage, Region} from "./reviewApi";
import "./manual-table.css";

type Merge = {row: number; column: number; row_span: number; column_span: number};
export interface ManualTableInput {
  revision: number; x_edges: number[]; y_edges: number[]; merges: Merge[];
  replace_region_ids: string[]; replace_table_id: string | null; note: string;
}
type Box = [number, number, number, number];
type GridDraft = {revision: number; bounds: Box; rowCount: string; columnCount: string; rowLines: string;
  columnLines: string; merges: Merge[]; replacement: string; selected: string[]; note: string};
function readDraft(key?: string): {draft?: GridDraft; warning?: string} {
  if (!key) return {};
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return {};
    if (raw.length > 200000) throw Error();
    const d = JSON.parse(raw);
    const numeric = (v: unknown) => v === null || typeof v === "number" && Number.isFinite(v);
    if (d.schema !== 1 || !Number.isInteger(d.revision) || d.revision < 1
      || !Array.isArray(d.bounds) || d.bounds.length !== 4 || !d.bounds.every(numeric)
      || ![d.rowCount, d.columnCount, d.rowLines, d.columnLines, d.replacement, d.note].every(v => typeof v === "string" && v.length <= 10000)
      || !Array.isArray(d.selected) || d.selected.length > 10000 || !d.selected.every((v: unknown) => typeof v === "string" && v.length <= 100)
      || !Array.isArray(d.merges) || d.merges.length > 200 || !d.merges.every((m: Merge) => m && [m.row, m.column, m.row_span, m.column_span].every(numeric))) throw Error();
    // JSON represents unfinished numeric inputs as null; restore their invalid state.
    d.bounds = d.bounds.map((v: number | null) => v === null ? NaN : v);
    d.merges = d.merges.map((m: Merge) => Object.fromEntries(["row", "column", "row_span", "column_span"].map(k => [k, m[k as keyof Merge] === null ? NaN : m[k as keyof Merge]])));
    return {draft: d};
  } catch { return {warning: "The browser could not restore a saved table draft. Check browser storage before leaving unfinished work."}; }
}
const overlap = (a: number[], b: number[]) => Math.min(a[2], b[2]) > Math.max(a[0], b[0]) && Math.min(a[3], b[3]) > Math.max(a[1], b[1]);
const untouched = (r: Region) => ["native", "image-ocr"].includes(r.method) && !r.table_id && !r.cell_id
  && r.kind === (r.method === "native" ? "paragraph" : "image") && !r.form_evidence_changed && !Object.keys(r.acknowledged ?? {}).length
  && r.form_evidence == null && r.source === r.raw && !r.translation.trim() && !r.source_approved && !r.translation_approved
  && !r.reason.trim() && !r.form_review_note?.trim() && r.source_language === "uncertain" && r.contains_text
  && r.disposition === (r.raw.trim() ? "translate" : "unreadable");

export function ManualTableEditor({page, previewUrl, disabled, onCreate, draftKey, onDirtyChange}: {
  page: ReviewPage; previewUrl: string; disabled: boolean; onCreate: (body: ManualTableInput) => Promise<void>;
  draftKey?: string; onDirtyChange?: (dirty: boolean) => void;
}) {
  const [initial] = useState(() => readDraft(draftKey));
  const [open, setOpen] = useState(!!initial.draft);
  const [recovered, setRecovered] = useState(!!initial.draft);
  const persisted = useRef(!!initial.draft);
  const [storageWarning, setStorageWarning] = useState(initial.warning ?? "");
  const [revision, setRevision] = useState(initial.draft?.revision ?? page.revision);
  const [bounds, setBounds] = useState<Box>(initial.draft?.bounds ?? [0, 0, page.width, page.height]);
  const [rowCount, setRowCount] = useState(initial.draft?.rowCount ?? "2"), [columnCount, setColumnCount] = useState(initial.draft?.columnCount ?? "2");
  const [rowLines, setRowLines] = useState(initial.draft?.rowLines ?? "0, 50, 100"), [columnLines, setColumnLines] = useState(initial.draft?.columnLines ?? "0, 50, 100");
  const [merges, setMerges] = useState<Merge[]>(initial.draft?.merges ?? []);
  const [replacement, setReplacement] = useState(initial.draft?.replacement ?? "");
  const [selected, setSelected] = useState<string[]>(initial.draft?.selected ?? []);
  const [note, setNote] = useState(initial.draft?.note ?? "");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false), [created, setCreated] = useState(false);
  const [imageReady, setImageReady] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const anchor = useRef<[number, number]>();
  const [drag, setDrag] = useState<Box>();
  const hasDraft = !created && (bounds.some((v, i) => v !== [0, 0, page.width, page.height][i])
    || rowCount !== "2" || columnCount !== "2" || rowLines !== "0, 50, 100" || columnLines !== "0, 50, 100"
    || merges.length > 0 || replacement !== "" || selected.length > 0 || note !== "");
  const serializedDraft = JSON.stringify({schema: 1, revision, bounds, rowCount, columnCount, rowLines, columnLines, merges, replacement, selected, note});
  useEffect(() => { onDirtyChange?.(hasDraft); }, [hasDraft, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  useEffect(() => {
    if (!draftKey) return;
    if (!hasDraft) { if (persisted.current) clearDraft(); return; }
    try {
      if (serializedDraft.length > 200000) throw Error("Draft exceeds browser recovery limit");
      localStorage.setItem(draftKey, serializedDraft); persisted.current = true; setStorageWarning("");
    }
    catch { setStorageWarning("This browser could not save your table draft. Keep this page open until you create the grid or explicitly discard the draft."); }
  }, [hasDraft, draftKey, serializedDraft]);
  function clearDraft() {
    setRecovered(false);
    if (!draftKey) return;
    try { localStorage.removeItem(draftKey); persisted.current = false; setStorageWarning(""); }
    catch { setStorageWarning("The old browser draft could not be removed. If it reappears after reload, discard it; the saved page remains authoritative."); }
  }
  const locked = disabled || working || revision !== page.revision;
  const stale = revision !== page.revision;
  const proposals = page.table_proposals?.tables ?? [];
  const intersecting = page.regions.filter(r => overlap(bounds, r.bbox));
  const crossing = intersecting.some(r => r.bbox[0] < bounds[0] || r.bbox[1] < bounds[1] || r.bbox[2] > bounds[2] || r.bbox[3] > bounds[3]);
  const protectedWork = intersecting.some(r => !untouched(r));
  const conflicting = proposals.some(t => t.id !== replacement && overlap(bounds, t.bbox) && t.decision?.action !== "not_table");
  let geometryError = "";
  let xs: number[] = [], ys: number[] = [], cells: (Merge & {box: Box})[] = [];
  try {
    const parse = (text: string, start: number, end: number, max: number) => {
      const parts = text.split(",");
      const values = parts.map(v => v.trim() ? Number(v) : NaN);
      if (values.length < 2 || values.length > max + 1 || values.some(v => !Number.isFinite(v)) || values[0] !== 0 || values.at(-1) !== 100)
        throw new Error("Boundary lists must start at 0 and end at 100, with a percentage for each dividing line.");
      const edges = values.map(v => start + (end - start) * v / 100);
      if (edges.some((v, i) => i && v - edges[i - 1] < 1)) throw new Error("Each cell must have a positive width and height of at least one PDF point.");
      return edges;
    };
    if (bounds.some(v => !Number.isFinite(v)) || bounds[0] < 0 || bounds[1] < 0 || bounds[2] > page.width || bounds[3] > page.height)
      throw new Error("Keep the table boundaries inside the original page.");
    xs = parse(columnLines, bounds[0], bounds[2], 20); ys = parse(rowLines, bounds[1], bounds[3], 40);
    const rows = ys.length - 1, columns = xs.length - 1;
    if (rows * columns > 200) throw new Error("Use at most 200 row and column positions per table.");
    const taken = new Set<string>();
    for (const m of merges) {
      if (Object.values(m).some(v => !Number.isInteger(v)) || m.row < 0 || m.column < 0 || m.row_span < 1 || m.column_span < 1
          || m.row + m.row_span > rows || m.column + m.column_span > columns) throw new Error("A merged cell is outside this grid.");
      for (let r = m.row; r < m.row + m.row_span; r++) for (let c = m.column; c < m.column + m.column_span; c++) {
        const key = `${r}:${c}`; if (taken.has(key)) throw new Error("Merged cells cannot overlap."); taken.add(key);
      }
      cells.push({...m, box: [xs[m.column], ys[m.row], xs[m.column + m.column_span], ys[m.row + m.row_span]]});
    }
    for (let r = 0; r < rows; r++) for (let c = 0; c < columns; c++) if (!taken.has(`${r}:${c}`))
      cells.push({row: r, column: c, row_span: 1, column_span: 1, box: [xs[c], ys[r], xs[c + 1], ys[r + 1]]});
  } catch (e) { geometryError = (e as Error).message; cells = []; }
  const mapped = selected.length === intersecting.length && intersecting.every(r => selected.includes(r.id));
  function changeBounds(box: Box) { setBounds(box); setSelected([]); setError(""); }
  function reset() {
    clearDraft();
    setRevision(page.revision); setBounds([0, 0, page.width, page.height]); setSelected([]); setReplacement("");
    setNote(""); setError(""); setCreated(false); setMerges([]); setRowLines("0, 50, 100"); setColumnLines("0, 50, 100"); setRowCount("2"); setColumnCount("2");
  }
  function useProposal(id: string) {
    setReplacement(id); setMerges([]); setSelected([]);
    const table = proposals.find(t => t.id === id);
    if (!table) { changeBounds([0, 0, page.width, page.height]); return; }
    changeBounds(table.bbox.slice() as Box);
    const percentages = (axis: 0 | 1) => [...new Set(table.cells.flatMap(c => [c.bbox[axis], c.bbox[axis + 2]]))].sort((a, b) => a - b)
      .map(v => 100 * (v - table.bbox[axis]) / (table.bbox[axis + 2] - table.bbox[axis]));
    const x = percentages(0), y = percentages(1);
    setColumnLines(x.length > 1 ? x.join(", ") : "0, 50, 100"); setRowLines(y.length > 1 ? y.join(", ") : "0, 50, 100");
    setColumnCount(String(Math.max(1, x.length - 1))); setRowCount(String(Math.max(1, y.length - 1)));
  }
  function evenLines() {
    const rows = Number(rowCount), columns = Number(columnCount);
    if (!Number.isInteger(rows) || !Number.isInteger(columns) || rows < 1 || rows > 40 || columns < 1 || columns > 20 || rows * columns > 200) {
      setError("Choose 1–40 rows and 1–20 columns, with at most 200 positions."); return;
    }
    const line = (n: number) => Array.from({length: n + 1}, (_, i) => i * 100 / n).join(", ");
    setRowLines(line(rows)); setColumnLines(line(columns)); setMerges([]); setError("");
  }
  function point(e: React.PointerEvent<SVGSVGElement>): [number, number] {
    const rect = e.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(page.width, (e.clientX - rect.left) / rect.width * page.width)),
      Math.max(0, Math.min(page.height, (e.clientY - rect.top) / rect.height * page.height))];
  }
  async function submit() {
    setWorking(true); setError("");
    try { await onCreate({revision, x_edges: xs, y_edges: ys, merges, replace_region_ids: selected, replace_table_id: replacement || null, note}); clearDraft(); setCreated(true); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save this grid. Your draft remains below."); }
    finally { setWorking(false); }
  }
  return <details className="manual-table-editor" open={open} onToggle={e => setOpen(e.currentTarget.open)}><summary>Build or correct a table grid</summary>
    {storageWarning && <p className="review-warning" role="alert">{storageWarning}</p>}
    {open && <>{created ? <div role="status"><p>Grid created. Use Table relationships to select each cell, transcribe its content or confirm it is blank, and review the structure.</p><button disabled={disabled} onClick={reset}>Start another grid</button></div> : <>
      <p>Draw around a complete table, then place its dividing lines. This creates cells for manual transcription; it does not recognise or approve their text.</p>
      {recovered && <p role="status">Recovered an unfinished table grid. Compare its boundaries and source selections with the current original before creating it.</p>}
      {hasDraft && <p role="status">Unfinished table grid: create it or discard this draft before changing pages or documents. <button disabled={disabled || working} onClick={reset}>Discard grid draft</button></p>}
      {stale && <p className="review-warning" role="alert">This page changed while you defined the grid. Compare the current page before starting again. <button disabled={disabled} onClick={reset}>Restart grid with current page</button></p>}
      <fieldset disabled={locked}><legend>Table area and boundaries</legend>
        <label>Detector proposal to replace<select aria-label="Detector proposal to replace" value={replacement} onChange={e => useProposal(e.target.value)}><option value="">New table</option>{proposals.filter(t => t.decision?.action !== "adopt").map((t, i) => <option key={t.id} value={t.id}>Detected table {i + 1} · {t.row_count} rows × {t.column_count} columns</option>)}</select></label>
        <div className="manual-table-preview">
          <img src={previewUrl} alt="Original page for defining table boundaries" onLoad={() => setImageReady(true)} onError={() => { setImageReady(false); setImageFailed(true); }}/>
          {imageReady && <svg viewBox={`0 0 ${page.width} ${page.height}`} aria-label="Draw table rectangle" role="img"
            onPointerDown={e => { if (locked) return; e.preventDefault(); anchor.current = point(e); e.currentTarget.setPointerCapture(e.pointerId); }}
            onPointerMove={e => { if (!anchor.current || locked) return; const p = point(e), a = anchor.current; setDrag([Math.min(a[0], p[0]), Math.min(a[1], p[1]), Math.max(a[0], p[0]), Math.max(a[1], p[1])]); }}
            onPointerUp={e => { if (!anchor.current) return; const p = point(e), a = anchor.current; anchor.current = undefined; setDrag(undefined); if (!locked && Math.abs(p[0] - a[0]) >= 1 && Math.abs(p[1] - a[1]) >= 1) changeBounds([Math.min(a[0], p[0]), Math.min(a[1], p[1]), Math.max(a[0], p[0]), Math.max(a[1], p[1])]); }}
            onPointerCancel={() => { anchor.current = undefined; setDrag(undefined); }}>
            {cells.map(c => <g key={`${c.row}:${c.column}`}><rect x={c.box[0]} y={c.box[1]} width={c.box[2] - c.box[0]} height={c.box[3] - c.box[1]}/><text x={c.box[0] + 2} y={c.box[1] + 12}>{c.row + 1},{c.column + 1}</text></g>)}
            {drag && <rect className="manual-table-drag" x={drag[0]} y={drag[1]} width={drag[2] - drag[0]} height={drag[3] - drag[1]}/>}
          </svg>}
        </div>
        {imageFailed && <p className="review-warning">The original preview could not load. Resolve the preview or RAM issue before creating this grid.</p>}
        <div className="manual-table-controls">{(["Left", "Top", "Right", "Bottom"] as const).map((name, i) => <label key={name}>{name} (% of page)<input type="number" min="0" max="100" step="any" aria-label={`Table ${name.toLowerCase()} percent`} value={Number.isFinite(bounds[i]) ? Number((bounds[i] / (i % 2 ? page.height : page.width) * 100).toFixed(6)) : ""} onChange={e => { const b = bounds.slice() as Box; b[i] = e.target.value === "" ? NaN : Number(e.target.value) * (i % 2 ? page.height : page.width) / 100; changeBounds(b); }}/></label>)}</div>
        <div className="manual-table-controls"><label>Rows<input type="number" aria-label="Manual table rows" min="1" max="40" value={rowCount} onChange={e => setRowCount(e.target.value)}/></label><label>Columns<input type="number" aria-label="Manual table columns" min="1" max="20" value={columnCount} onChange={e => setColumnCount(e.target.value)}/></label><button onClick={evenLines}>Space boundaries evenly</button></div>
        <p className="muted">For unequal cells, edit the percentages below. Example: 0, 30, 100 makes two columns, with the first using 30% of the table width.</p>
        <label>Column boundaries (% across table)<input aria-label="Column boundaries percent" value={columnLines} onChange={e => setColumnLines(e.target.value)}/></label>
        <label>Row boundaries (% down table)<input aria-label="Row boundaries percent" value={rowLines} onChange={e => setRowLines(e.target.value)}/></label>
        <details><summary>Merged cells</summary><p>Row and column numbers start at 1. Enter how many rows and columns each merged cell covers.</p>{merges.map((m, i) => <div className="manual-table-controls" key={i}>{(["row", "column", "row_span", "column_span"] as const).map(k => <label key={k}>{({row:"Start row", column:"Start column", row_span:"Rows covered", column_span:"Columns covered"})[k]}<input type="number" min="1" aria-label={`Merge ${i + 1} ${k}`} value={Number.isNaN(m[k]) ? "" : m[k] + (k === "row" || k === "column" ? 1 : 0)} onChange={e => setMerges(merges.map((item, n) => n !== i ? item : {...item, [k]: e.target.value === "" ? NaN : Number(e.target.value) - (k === "row" || k === "column" ? 1 : 0)}))}/></label>)}<button onClick={() => setMerges(merges.filter((_, n) => n !== i))}>Remove merge {i + 1}</button></div>)}<button disabled={locked || merges.length >= 200} onClick={() => setMerges([...merges, {row:0, column:0, row_span:1, column_span:1}])}>Add merged cell</button></details>
      </fieldset>
      {geometryError && <p className="review-warning" role="alert">{geometryError}</p>}
      {crossing && <p className="review-warning">A source region crosses this rectangle. It cannot be replaced because content outside the table would be lost. A partial table within a full-page scan needs a separate region-splitting workflow.</p>}
      {protectedWork && <p className="review-warning">This area includes saved edits, approvals, form fields or existing table cells. Keep that work; this grid cannot replace it.</p>}
      {conflicting && <p className="review-warning">This area overlaps another table proposal. Choose the intended detector proposal or adjust the rectangle.</p>}
      <fieldset disabled={locked}><legend>Source regions to replace ({intersecting.length})</legend><p>Only the selected untouched recognition regions will move into the table’s source-reference archive. Review their original text below. Nothing is copied into the new cells automatically.</p>
        <div className="manual-table-region-list">{intersecting.map(r => <label className="checkbox-label" key={r.id}><input type="checkbox" aria-label={`Replace source region ${r.order + 1}`} checked={selected.includes(r.id)} onChange={e => setSelected(e.target.checked ? [...selected, r.id] : selected.filter(id => id !== r.id))}/><span>Region {r.order + 1} · {r.kind}<pre>{r.source || r.raw || "No text recognised; inspect the original image."}</pre></span></label>)}</div>
        {!intersecting.length && <p>No existing source region is inside this rectangle. Confirm against the original that this is a missed table.</p>}
        <label>Grid and replacement review note<textarea aria-label="Manual table review note" rows={3} value={note} onChange={e => setNote(e.target.value)} placeholder="Explain the boundaries, merged cells and source regions you checked."/></label>
      </fieldset>
      {error && <p className="review-warning" role="alert">{error}</p>}
      <p className="muted">New cells start unresolved, including cells that appear blank. Review every cell and the full page after creating the grid.</p>
      <button className="primary" disabled={locked || !imageReady || !!geometryError || crossing || protectedWork || conflicting || !mapped || note.trim().length < 10} onClick={() => void submit()}>{working ? "Creating grid…" : "Create unreviewed table grid"}</button>
    </>}</>}
  </details>;
}
