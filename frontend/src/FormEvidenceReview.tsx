import React, {useEffect, useRef, useState} from "react";
import {Region, ReviewPage} from "./reviewApi";

export interface FormEvidencePreview {
  id: string;
  page_revision: number;
  document_revision: number;
  inspection: {
    regions: Region[];
    form_evidence: {widgets: unknown[]; inventory_complete: boolean};
  };
  suggestions: Record<string, string | null>;
}

export interface FormEvidenceBinding {widget_xref: number; region_id: string | null}

export interface FormEvidenceReviewProps {
  page: ReviewPage;
  busy: boolean;
  onPreview: () => Promise<FormEvidencePreview>;
  onApply: (preview: FormEvidencePreview, bindings: FormEvidenceBinding[], note: string) => Promise<void>;
}

interface FieldEvidence {
  xref?: number;
  field_name?: string;
  field_type?: string;
  stored_value?: unknown;
  bbox?: number[];
  appearance?: {text?: string | null; text_available?: boolean};
  warnings?: string[];
  discrepancies?: {message?: string}[];
}

const NEW_REGION = "__new__";
const PAGE_SIZE = 10;
const evidenceOf = (region: Region) => region.form_evidence as FieldEvidence | undefined;
function valueText(value: unknown): string {
  if (value === undefined || value === null) return "No recorded value";
  if (value === "") return "Empty value";
  if (typeof value === "string") return value;
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function regionLabel(region: Region) {
  const name = evidenceOf(region)?.field_name;
  const text = region.source || region.raw || "No transcription";
  return `Region ${region.order + 1}${name ? ` · ${name}` : ""} · ${text.slice(0, 90)}${text.length > 90 ? "…" : ""}`;
}

// The caller keys this component by document ID and page number. A preview is
// never an edit: the server rechecks its revisions and the submitted identities.
export function FormEvidenceReview({page, busy, onPreview, onApply}: FormEvidenceReviewProps) {
  const [preview, setPreview] = useState<FormEvidencePreview | null>(null);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [offset, setOffset] = useState(0);
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);

  const existing = page.regions.filter(region => region.method === "form-field").sort((a, b) => a.order - b.order);
  const proposed = preview?.inspection.regions ?? [];
  const disabled = busy || pending;
  const stale = preview !== null && preview.page_revision !== page.revision;
  const existingIds = new Set(existing.map(region => region.id));
  const mapped = new Map<string, number>();
  const xrefs = new Set<number>();
  let incomplete = false;
  let invalidIdentity = false;
  for (const region of proposed) {
    const xref = evidenceOf(region)?.xref;
    if (!Number.isInteger(xref) || !xref || xrefs.has(xref)) {
      invalidIdentity = true;
      continue;
    }
    xrefs.add(xref);
    const choice = choices[String(xref)];
    if (!choice || (choice !== NEW_REGION && !existingIds.has(choice))) incomplete = true;
    else if (choice !== NEW_REGION) mapped.set(choice, (mapped.get(choice) ?? 0) + 1);
  }
  const unmapped = existing.filter(region => !mapped.has(region.id));
  const duplicated = existing.filter(region => (mapped.get(region.id) ?? 0) > 1);
  const inventoryComplete = preview?.inspection.form_evidence.inventory_complete === true;
  const canApply = !!preview && inventoryComplete && !stale && !invalidIdentity && !incomplete
    && !unmapped.length && !duplicated.length && note.trim().length >= 10 && !disabled;

  async function inspect() {
    const request = ++generation.current;
    setPending(true);
    setError("");
    setMessage("");
    try {
      const result = await onPreview();
      if (generation.current !== request) return;
      const initial: Record<string, string> = {};
      for (const region of result.inspection.regions) {
        const xref = evidenceOf(region)?.xref;
        if (xref === undefined) continue;
        if (Object.prototype.hasOwnProperty.call(result.suggestions, String(xref))) {
          initial[String(xref)] = result.suggestions[String(xref)] ?? NEW_REGION;
        }
      }
      setPreview(result);
      setChoices(initial);
      setOffset(0);
    } catch (failure) {
      if (generation.current === request) setError(failure instanceof Error ? failure.message : "Form evidence could not be inspected. Saved content is unchanged.");
    } finally {
      if (generation.current === request) setPending(false);
    }
  }

  async function apply() {
    if (!preview || !canApply) return;
    const request = ++generation.current;
    const bindings = proposed.map(region => {
      const xref = evidenceOf(region)!.xref!;
      return {widget_xref: xref, region_id: choices[String(xref)] === NEW_REGION ? null : choices[String(xref)]};
    });
    setPending(true);
    setError("");
    setMessage("");
    try {
      await onApply(preview, bindings, note.trim());
      if (generation.current !== request) return;
      setPreview(null);
      setChoices({});
      setNote("");
      setOffset(0);
      setMessage("Form evidence mappings applied. Review any changed evidence and newly added fields before approving content.");
    } catch (failure) {
      if (generation.current === request) setError(failure instanceof Error ? failure.message : "Mappings could not be applied. The preview is retained for review.");
    } finally {
      if (generation.current === request) setPending(false);
    }
  }

  return <details className="structure-panel">
    <summary>Refresh form evidence</summary>
    <p>Inspect the original form fields, then check which saved region each field belongs to. Inspection does not change your work.</p>
    <p className="review-warning">Saved source text, translations and manual edits are preserved. New fields remain unreviewed. Changed evidence resets the affected approvals and requires another completeness review.</p>
    <button disabled={disabled} onClick={() => void inspect()}>{pending && !preview ? "Inspecting form evidence…" : "Inspect form evidence"}</button>
    {error && <p className="review-warning" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {preview && <div>
      <p>{proposed.length} original form fields · {existing.length} saved form regions. Check every mapping before applying it.</p>
      {!inventoryComplete && <p className="review-warning" role="alert">The field inventory is incomplete. These mappings cannot be applied; resolve the inspection issue and inspect again.</p>}
      {stale && <p className="review-warning" role="alert">This page changed after inspection. Inspect again before applying mappings.</p>}
      {invalidIdentity && <p className="review-warning" role="alert">The inspection contains missing or duplicate field identities. Inspect again; no mappings can be applied.</p>}
      {proposed.slice(offset, offset + PAGE_SIZE).map((region, index) => {
        const field = evidenceOf(region);
        const xref = field?.xref;
        return <div className="glossary-entry" key={`${xref ?? "unknown"}:${offset + index}`}>
          <h4>Field {offset + index + 1}: {field?.field_name || "Unnamed field"}</h4>
          <p className="muted">{field?.field_type || "Unknown field type"} · Original field ID {xref ?? "unavailable"}{field?.bbox && ` · Bounds ${field.bbox.map(n => Number.isFinite(n) ? n.toFixed(1) : "?").join(", ")}`}</p>
          <strong>Stored value</strong><pre>{valueText(field?.stored_value)}</pre>
          <strong>Text from the existing appearance</strong><pre>{field?.appearance?.text_available === false || field?.appearance?.text == null ? "No readable appearance text. Compare the visible field in the original page." : valueText(field.appearance.text)}</pre>
          {field?.discrepancies?.map((item, i) => item.message && <p className="review-warning" key={i}>{item.message}</p>)}
          {!!field?.warnings?.length && <details><summary>Field inspection notes</summary>{field.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</details>}
          <label>Saved region for field {offset + index + 1}
            <select aria-label={`Saved region for field ${offset + index + 1}`} disabled={disabled || stale || xref === undefined} value={xref === undefined ? "" : choices[String(xref)] ?? ""}
              onChange={event => setChoices(previous => ({...previous, [String(xref)]: event.target.value}))}>
              <option value="">Choose the matching region</option>
              <option value={NEW_REGION}>Add a separate field region</option>
              {existing.map(saved => <option value={saved.id} key={saved.id}>{regionLabel(saved)}</option>)}
            </select>
          </label>
        </div>;
      })}
      {proposed.length > PAGE_SIZE && <div className="button-row">
        <button disabled={pending || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous fields</button>
        <span>Fields {offset + 1}–{Math.min(offset + PAGE_SIZE, proposed.length)} of {proposed.length}</span>
        <button disabled={pending || offset + PAGE_SIZE >= proposed.length} onClick={() => setOffset(offset + PAGE_SIZE)}>Next fields</button>
      </div>}
      {incomplete && <p className="review-warning">Choose a mapping for every original field, including fields on the other preview pages.</p>}
      {!!unmapped.length && <p className="review-warning">{unmapped.length} saved form regions still need a matching original field: {unmapped.slice(0, 10).map(region => `region ${region.order + 1}`).join(", ")}{unmapped.length > 10 ? ", …" : ""}. Every saved region must be mapped exactly once.</p>}
      {!!duplicated.length && <p className="review-warning">The same saved region is selected for more than one original field. Select a separate matching region for each field.</p>}
      <label>Evidence mapping review note
        <textarea aria-label="Evidence mapping review note" rows={3} value={note} disabled={disabled}
          onChange={event => setNote(event.target.value)} placeholder="Describe how you checked the field identities and their matching regions." maxLength={4000}/>
      </label>
      <p className="muted">Use at least 10 characters. Applying mappings records this review; it does not approve the field contents.</p>
      <button className="primary" disabled={!canApply} onClick={() => void apply()}>Apply evidence mappings</button>
    </div>}
  </details>;
}
