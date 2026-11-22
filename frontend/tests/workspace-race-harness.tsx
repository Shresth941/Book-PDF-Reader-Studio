// Synthetic transport for the real workspace. No PDF, model or user workspace is used.
import React, {useState} from "react";
import {createRoot} from "react-dom/client";
import {ReviewWorkspace} from "../src/ReviewWorkspace";
import {ReviewDocument, ReviewPage, Region} from "../src/reviewApi";

const documents: ReviewDocument[] = ["Alpha", "Beta"].map((name, i) => ({
  id: name, filename: `${name}.pdf`, page_count: i ? 1 : 2, revision: 1, status: "ready",
  target: "hi", domain: "general", jurisdiction: "", review_status: "ready_for_review", metadata: {},
  progress: {extracted: i ? 1 : 2, coverage_approved: 0, content_approved: 0},
}));
const pages = new Map<string, ReviewPage>();
for (const d of documents) for (let n = 1; n <= d.page_count; n++) {
  const text = `${d.id} source page ${n}`;
  const r: Region = {id: `${d.id}-${n}`, order: 0, bbox: [20, 20, 260, 80], kind: "paragraph",
    raw: text, source: text, translation: "", source_language: "en", language_hint: "latin",
    disposition: "translate", contains_text: true, reason: "", method: "native", source_approved: false,
    translation_approved: false, acknowledged: {}, alternatives: [], evidence: [], checks: [{code: "synthetic", message: "Verify the synthetic amount", blocking: true}]};
  pages.set(`${d.id}/${n}`, {number: n, revision: 1, width: 300, height: 400, status: "extracted",
    regions: [r], coverage_approved: false, blockers: [], error: "", table_proposals: {status: "none", tables: [], warnings: [{code: "synthetic", message: "Synthetic structure warning"}]}});
}
let holdPage = false, holdSave = false, polling = false, failApproval = false, failStorage = false;
const realSetItem = Storage.prototype.setItem;
Storage.prototype.setItem = function(key: string, value: string) {
  if (failStorage) throw new DOMException("Synthetic quota exhaustion", "QuotaExceededError");
  return realSetItem.call(this, key, value);
};
let pending: (() => void)[] = [];
let notify = () => {};
const response = (value: unknown) => new Response(JSON.stringify(value), {headers: {"Content-Type": "application/json"}});
window.fetch = async (input, init) => {
  const path = new URL(String(input), location.href).pathname.replace("/api/v2", "");
  const method = init?.method ?? "GET";
  if (path === "/session") return response({token: "synthetic-only"});
  if (path === "/documents") return response({documents});
  if (path === "/capabilities") return response({ocr: {installed: false}, translation: {},
    structure: {reason: "Synthetic structure fixture", manual_tables: true}, cloud: {available: false, reason: "Offline fixture"}});
  const match = path.match(/^\/documents\/(Alpha|Beta)(.*)$/);
  if (!match) throw Error(`Unimplemented fixture request: ${method} ${path}`);
  const [, id, suffix] = match;
  if (!suffix) return response(documents.find(d => d.id === id));
  if (suffix === "/jobs") return response({jobs: polling && id === "Alpha" ? [{id: "fixture-job", kind: "extract", status: "running", cursor: 0, message: "Synthetic poll", payload: {pages: [1, 2]}}] : []});
  if (suffix === "/exports") return response({exports: []});
  if (suffix === "/glossary") return response({entries: []});
  const p = suffix.match(/^\/pages\/(\d+)(?:\/regions\/[^/]+|\/approvals)?$/);
  if (!p) throw Error(`Unimplemented fixture request: ${method} ${path}`);
  const page = pages.get(`${id}/${p[1]}`)!;
  if (method === "POST" && suffix.endsWith("/approvals")) {
    if (failApproval) return new Response(JSON.stringify({detail: "Synthetic approval failure"}), {status: 409});
    const body = JSON.parse(String(init!.body));
    if (body.kind === "source") page.regions[0].source_approved = true;
    if (body.kind === "translation") {page.regions[0].translation_approved = true; page.regions[0].acknowledged = body.acknowledgements;}
    if (body.kind === "coverage") {page.coverage_approved = true; page.structure_warning_review = {note: body.acknowledgements.structure_warnings};}
  }
  if (method === "PATCH") {
    const body = JSON.parse(String(init!.body));
    const {revision: _revision, reviewer: _reviewer, ...edits} = body;
    Object.assign(page.regions[0], edits); page.revision++;
  }
  const captured = structuredClone(page);
  if ((method === "GET" && holdPage) || (method === "PATCH" && holdSave)) {
    if (method === "GET") holdPage = false; else holdSave = false;
    return new Promise<Response>(resolve => {pending.push(() => resolve(response(captured))); notify();});
  }
  return response(captured);
};
function Harness() {
  const [, redraw] = useState(0);
  notify = () => redraw(n => n + 1);
  return <><aside style={{padding: 12, background: "#f9e8a8"}}>
    <strong>Synthetic delayed-response checks — no real document processing</strong>
    <div><button onClick={() => {polling = true; notify();}}>Enable Alpha polling (reopen document)</button>
      <button onClick={() => {holdPage = true; notify();}}>Hold next page response</button>
      <button onClick={() => {holdSave = true; notify();}}>Hold next save response</button>
      <button onClick={() => {polling = false; const all = pending; pending = []; all.forEach(release => release()); notify();}}>Release held responses and stop polling</button>
      <button onClick={() => {failApproval = !failApproval; notify();}}>Toggle approval failure</button>
      <button onClick={() => {failStorage = !failStorage; notify();}}>Toggle draft storage failure</button>
    </div><output>Held responses: {pending.length}; page armed: {String(holdPage)}; save armed: {String(holdSave)}</output>
    <output>Approval failure: {String(failApproval)}; storage failure: {String(failStorage)}</output>
  </aside><ReviewWorkspace/></>;
}
createRoot(document.getElementById("root")!).render(<Harness/>);
