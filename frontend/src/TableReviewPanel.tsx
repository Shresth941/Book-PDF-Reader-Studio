import React, {useState} from "react";
import {ReviewPage, TableProposal} from "./reviewApi";

export function TableReviewPanel({page, disabled, review, select}: {
  page: ReviewPage; disabled: boolean;
  review: (id: string, action: string, note: string) => void;
  select: (id: string) => void;
}) {
  const [active, setActive] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [archivePages, setArchivePages] = useState<Record<string, number>>({});
  const proposals = page.table_proposals;
  if (!proposals || (!proposals.tables.length && !proposals.warnings.length)) return null;
  function grid(table: TableProposal) {
    const cells = table.cells.slice(0, 200);
    const rows = [...new Set(cells.map(cell => cell.row))];
    return <div className="table-scroll"><table className="structure-table"><tbody>{rows.map(row => <tr key={row}>{cells.filter(c => c.row === row).map(cell => {
      const region = page.regions.find(r => r.cell_id === cell.id && r.table_id === table.id);
      return <td key={cell.id} rowSpan={cell.row_span} colSpan={cell.column_span}>
        <small>Row {cell.row + 1}, column {cell.column + 1}</small>
        {region ? <button disabled={disabled} onClick={() => select(region.id)}>{region.source || (region.disposition === "unreadable" ? "Unresolved cell" : region.disposition === "decorative" ? "Empty cell" : "No transcription")}</button> : <span>{cell.raw ?? "Unresolved cell"}</span>}
      </td>;
    })}</tr>)}</tbody></table>{table.cells.length > cells.length && <p>Preview shows the first 200 cells. Check the full original and use the region selector for every remaining cell.</p>}</div>;
  }
  return <section className="structure-panel"><h3>Table relationships</h3>
    <p>Compare every value with its row and column headers. Cell detection does not approve content.</p>
    {proposals.warnings.map(w => <p className="review-warning" key={w.code}>{w.message}</p>)}
    {proposals.tables.map((table, index) => {
      const note = notes[table.id] ?? table.structure_review?.note ?? table.decision?.note ?? "";
      return <div key={table.id}><button className="table-summary" onClick={() => setActive(active === table.id ? "" : table.id)}>Table {index + 1} · {table.row_count} rows × {table.column_count} columns · {table.structure_approved ? "structure reviewed" : table.decision?.action === "not_table" ? "marked not a table" : "needs review"}</button>
        {active === table.id && <div>{grid(table)}{table.warnings.map((w, i) => <p className="review-warning" key={i}>{w.message}</p>)}
          {table.method === "manual_grid" && <p className="review-warning">This is a manually defined grid. Every cell started unresolved; an empty transcription is not proof of a blank cell. Check the original and retained extraction.</p>}
          {!!table.original_regions?.length && <details className="table-source-archive"><summary>Preserved source extraction ({table.original_regions.length} regions)</summary>
            <p>This is reference evidence from before the grid was created. It has not been copied into the new cells or silently approved.</p>
            {table.original_regions.slice((archivePages[table.id] ?? 0) * 10, (archivePages[table.id] ?? 0) * 10 + 10).map(r => <div key={r.id}><strong>Original region {r.order + 1} · {r.method}</strong><pre>{r.raw || "No native text was recognised."}</pre>{r.source !== r.raw && <pre>{r.source}</pre>}{r.evidence.map((e, i) => <p key={i}>{e}</p>)}{r.alternatives.map((a, i) => <details key={i}><summary>Recognition alternative {i + 1} · {a.method}</summary><p>{a.error || a.warning}</p><pre>{a.text}</pre></details>)}</div>)}
            {table.original_regions.length > 10 && <div className="button-row"><button disabled={!(archivePages[table.id] ?? 0)} onClick={() => setArchivePages({...archivePages, [table.id]: (archivePages[table.id] ?? 0) - 1})}>Previous source regions</button><button disabled={((archivePages[table.id] ?? 0) + 1) * 10 >= table.original_regions.length} onClick={() => setArchivePages({...archivePages, [table.id]: (archivePages[table.id] ?? 0) + 1})}>Next source regions</button></div>}
          </details>}
          <label>Table review note<textarea aria-label={`Table ${index + 1} review note`} rows={2} value={note} onChange={e => setNotes({...notes, [table.id]: e.target.value})}/></label>
          <div className="button-row">{table.decision?.action === "adopt" ? <button disabled={disabled || note.trim().length < 10} onClick={() => review(table.id, "approve_structure", note)}>I checked headers, values, blank cells and spans</button> : <>
            <button disabled={disabled || note.trim().length < 10 || table.status === "ambiguous"} onClick={() => review(table.id, "adopt", note)}>Create reviewable table cells</button>
            <button disabled={disabled || note.trim().length < 10} onClick={() => review(table.id, "not_table", note)}>This is not a table</button></>}</div>
          <p className="muted">For an unadopted or missed table, use Build or correct a table grid below. It protects saved edits and requires every cell to be reviewed. Do not mark a real table as “not a table” to bypass review.</p>
        </div>}
      </div>;
    })}
  </section>;
}
