import {useState} from "react";
import type {Region, ReviewPage} from "./reviewApi";

const editionFile = "/references/constitution-2015-en.pdf";

export function PublishedEnglish({location}: {location: NonNullable<ReviewPage["english_reference"]>}) {
  const url = editionFile + "#page=" + location.page;
  return <section className="reader-reference" aria-label="Published English edition">
    <h3>Constitution of India · 9 November 2015</h3>
    <p>Government of India, Legislative Department. {location.label}. English PDF page {location.page}.</p>
    <p><a href={url} target="_blank" rel="noreferrer">Open English edition</a>{" · "}<a href={editionFile} download>Download published PDF</a></p>
    <iframe title="Constitution of India, published English text" key={url} src={url}/>
  </section>;
}

export function GoogleComparison({regions, source, target}: {regions: Region[]; source: string; target: string}) {
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const passages = regions.filter(r => r.contains_text && r.source.trim());
  if (!passages.length) return null;
  const text = passages.find(r => r.id === selected)?.source || passages[0].source;
  return <details className="reader-help"><summary>Compare a passage in Google Translate</summary>
    <label>Passage<select value={passages.some(r => r.id === selected) ? selected : passages[0].id}
      onChange={event => {setSelected(event.target.value); setMessage("");}}>
      {passages.map((r, i) => <option key={r.id} value={r.id}>{i + 1}. {r.source.slice(0, 55)}</option>)}
    </select></label>
    <textarea aria-label="Passage to compare" readOnly value={text} rows={3}/>
    <p>Copy the passage, then paste it into Google Translate if you agree to share it with Google.</p>
    <button type="button" onClick={async () => {
      try {await navigator.clipboard.writeText(text); setMessage("Passage copied.");}
      catch {setMessage("Select the passage above and copy it manually.");}
    }}>Copy passage</button>{" "}
    <a href={`https://translate.google.com/?sl=${source}&tl=${target}&op=translate`} target="_blank" rel="noreferrer">Open Google Translate</a>
    <span role="status">{message}</span>
  </details>;
}
