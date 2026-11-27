import {useState} from "react";
import {reviewCall, type Region, type ReviewPage} from "./reviewApi";

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

export function GoogleComparison({regions, source, target, path, revision, configured, disabled}: {
  regions: Region[]; source: string; target: string; path: string; revision?: number; configured: boolean; disabled: boolean;
}) {
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState("");
  const [consent, setConsent] = useState(false);
  const passages = regions.filter(r => r.contains_text && r.source.trim());
  if (!passages.length) return null;
  const passage = passages.find(r => r.id === selected) || passages[0];
  const text = passage.source;
  return <details className="reader-help"><summary>Compare a passage in Google Translate</summary>
    <label>Passage<select disabled={pending || disabled} value={passages.some(r => r.id === selected) ? selected : passages[0].id}
      onChange={event => {setSelected(event.target.value); setMessage(""); setConsent(false); setResult("");}}>
      {passages.map((r, i) => <option key={r.id} value={r.id}>{i + 1}. {r.source.slice(0, 55)}</option>)}
    </select></label>
    <textarea aria-label="Passage to compare" readOnly value={text} rows={3}/>
    {configured && <>
      <label><input type="checkbox" disabled={disabled || pending} checked={consent} onChange={e => setConsent(e.target.checked)}/>
        I approve sending this public, nonpersonal passage to Google Cloud. Project usage charges may apply.</label>
      <button disabled={disabled || !revision || !consent || pending || source === target || Array.from(text).length > 5000} onClick={async () => {
        setPending(true); setConsent(false); setMessage(""); setResult("");
        try {
          const answer = await reviewCall<{translation: string}>(`${path}/regions/${passage.id}/google-compare`, "POST",
            {revision, source, target, text, public_nonpersonal: true, approve_send: true});
          setResult(answer.translation);
        } catch (e) {setMessage(e instanceof Error ? e.message : "Google comparison failed.");}
        finally {setPending(false);}
      }}>{pending ? "Waiting for Google…" : "Translate passage with Google Cloud"}</button>
      {Array.from(text).length > 5000 && <p>Cloud comparison supports passages up to 5,000 characters.</p>}
      {result && <p lang={target}><strong>Google Cloud draft</strong><br/>{result}</p>}
    </>}
    <p>Copy the passage, then paste it into Google Translate if you agree to share it with Google.</p>
    <button type="button" onClick={async () => {
      try {await navigator.clipboard.writeText(text); setMessage("Passage copied.");}
      catch {setMessage("Select the passage above and copy it manually.");}
    }}>Copy passage</button>{" "}
    <a href={`https://translate.google.com/?sl=${source}&tl=${target}&op=translate`} target="_blank" rel="noreferrer">Open Google Translate</a>
    <span role="status">{message}</span>
  </details>;
}
