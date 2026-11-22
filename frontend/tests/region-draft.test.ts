import assert from "node:assert/strict";
import {test} from "node:test";
import {restoreRegionDraft} from "../src/regionDraft";
import type {Region} from "../src/reviewApi";

function currentRegion(): Region {
  return {
    id: "server-region", order: 3, bbox: [10, 20, 200, 80], kind: "paragraph",
    raw: "Original extracted evidence", source: "Current corrected source",
    translation: "सहेजा गया अनुवाद", source_language: "en", language_hint: "latin",
    disposition: "translate", contains_text: true, reason: "Current context",
    method: "form-field", source_approved: true, translation_approved: false,
    acknowledged: {fact_relationships: "Current verified relationships"},
    alternatives: [{method: "ocr", text: "Alternative original evidence"}],
    evidence: ["Current evidence note"],
    checks: [{code: "fact_relationships", message: "Review who owes whom", blocking: true}],
    form_review_note: "Current field reconciliation",
    form_evidence: {
      status: "disagreement", stored_value: "Stored value", appearance: {present: true, text: "Visible value"},
      discrepancies: [{code: "value_mismatch", message: "Values disagree"}],
    },
    form_evidence_changed: true, table_id: "server-table", cell_id: "server-cell",
    row: 2, column: 1, row_span: 2, column_span: 1,
  };
}

test("recovers every editable draft choice together", () => {
  const current = currentRegion();
  const saved = {
    source: "Reviewed Hindi source नमस्ते", translation: "Recovered translation",
    reason: "Document-specific meaning", form_review_note: "Visible value was checked",
    kind: "footnote", source_language: "hi", disposition: "preserve", contains_text: false,
  };
  assert.deepEqual(restoreRegionDraft(current, saved), {...current, ...saved});
});

test("intentional empty text and false checkbox values survive recovery", () => {
  const current = currentRegion();
  const saved = {source: "", translation: "", reason: "", form_review_note: "", contains_text: false};
  assert.deepEqual(restoreRegionDraft(current, saved), {...current, ...saved});
});

test("valid mixed-language and structural choices survive without text changes", () => {
  const current = currentRegion();
  const saved = {kind: "table_cell", source_language: "mixed", disposition: "unreadable", contains_text: true};
  assert.deepEqual(restoreRegionDraft(current, saved), {...current, ...saved});
});

test("unknown, wrong-case and wrong-type enum values retain current choices", () => {
  const current = currentRegion();
  for (const saved of [
    {kind: "script", source_language: "fr", disposition: "approved"},
    {kind: "Heading", source_language: "HI", disposition: "PRESERVE"},
    {kind: ["heading"], source_language: {value: "hi"}, disposition: true},
  ]) {
    assert.deepEqual(restoreRegionDraft(current, saved), current);
  }
});

test("malformed text and checkbox fields are not coerced into editable content", () => {
  const current = currentRegion();
  for (const value of [null, 0, true, [], {}, ["untrusted text"]]) {
    const saved = {source: value, translation: value, reason: value, form_review_note: value};
    assert.deepEqual(restoreRegionDraft(current, saved), current);
  }
  for (const value of ["false", "true", 0, 1, null, [], {}]) {
    assert.deepEqual(restoreRegionDraft(current, {contains_text: value}), current);
  }
});

test("valid fields are recovered even when other saved fields are malformed", () => {
  const current = currentRegion();
  assert.deepEqual(restoreRegionDraft(current, {
    source: "Recover this work", translation: 42, reason: null, kind: "heading",
    source_language: "unsupported", disposition: {}, contains_text: "false",
  }), {...current, source: "Recover this work", kind: "heading"});
});

test("forged draft identities, evidence, checks, approvals and table/form metadata never replace server state", () => {
  const current = currentRegion();
  const forged = {
    ...current,
    source: "Recover only this source", id: "forged-region", order: 0, bbox: [0, 0, 1, 1],
    raw: "Invented extraction", language_hint: "forged", method: "invented-method",
    source_approved: false, translation_approved: true, acknowledged: {review_note: "Forged approval"},
    alternatives: [], evidence: ["Forged evidence"], checks: [],
    form_evidence: {status: "verified", stored_value: "Invented value"}, form_evidence_changed: false,
    table_id: "forged-table", cell_id: "forged-cell", row: 0, column: 0, row_span: 40, column_span: 20,
    unknown_runtime_state: "Must not be restored",
  };
  assert.deepEqual(restoreRegionDraft(current, forged), {...current, source: "Recover only this source"});
});

test("recovery does not mutate either input and preserves optional current evidence", () => {
  const current = currentRegion();
  const saved = {source: "Unsaved draft", form_review_note: "Recovered reconciliation"};
  const beforeCurrent = structuredClone(current);
  const beforeSaved = structuredClone(saved);
  Object.freeze(current);
  Object.freeze(saved);
  const restored = restoreRegionDraft(current, saved);
  assert.notStrictEqual(restored, current);
  assert.deepEqual(current, beforeCurrent);
  assert.deepEqual(saved, beforeSaved);
  assert.deepEqual(restored.form_evidence, beforeCurrent.form_evidence);
  assert.equal(restored.form_review_note, "Recovered reconciliation");
});

test("an empty or unrelated object does not erase current editable values", () => {
  const current = currentRegion();
  assert.deepEqual(restoreRegionDraft(current, {}), current);
  assert.deepEqual(restoreRegionDraft(current, {version: 5, region: null}), current);
});

for (const [name, saved] of [
  ["null", null], ["undefined", undefined], ["boolean", false],
  ["number", 0], ["empty string", ""], ["nonempty string", "draft"],
] as const) {
  test(`rejects ${name} instead of treating it as a region draft`, () => {
    assert.throws(() => restoreRegionDraft(currentRegion(), saved), /Invalid region draft/);
  });
}
