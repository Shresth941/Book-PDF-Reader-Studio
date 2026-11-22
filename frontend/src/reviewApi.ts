// A packaged local build always talks to its own loopback server. Old hosted
// environment values must never redirect private documents to another origin.
const BASE = import.meta.env.DEV ? "http://127.0.0.1:8000" : "";
let session: Promise<string> | undefined;
export function resetReviewSession() { session = undefined; }
function resetUnauthorized(status: number) {
  if (status === 401 || status === 403) resetReviewSession();
  if (status === 401 && import.meta.env.VITE_RUNTIME_MODE === "vercel") {
    window.dispatchEvent(new Event("pdf-review-auth-required"));
  }
}
export const reviewUrl = (path: string) => `${BASE}/api/v2${path}`;
async function token() {
  session ??= fetch(reviewUrl("/session")).then(async r => {
    if (!r.ok) {
      resetUnauthorized(r.status);
      throw new Error("Cannot open the review session. Sign in again or retry.");
    }
    return (await r.json()).token as string;
  }).catch(e => { session = undefined; throw e; });
  return session;
}
export async function reviewCall<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-Review-Token"] = await token();
  if (body && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(reviewUrl(path), { method, headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  if (!response.ok) {
    resetUnauthorized(response.status);
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status}). Check the entered values.`);
  }
  return response.json() as Promise<T>;
}
export interface ReviewDocument {
  id: string; filename: string; page_count: number; revision: number; status: string;
  target: "en" | "hi"; domain: string; jurisdiction: string; review_status: string;
  metadata: { message?: string; code?: string; attachments?: string[]; form_review_required?: boolean };
  progress: { extracted: number; coverage_approved: number; content_approved: number };
}
export interface Check {code: string; message: string; blocking: boolean}
export interface Region {
  id: string; order: number; bbox: [number, number, number, number]; kind: string;
  raw: string; source: string; translation: string; source_language: string; language_hint: string;
  disposition: string; contains_text: boolean; reason: string; method: string;
  source_approved: boolean; translation_approved: boolean; acknowledged: Record<string, string>;
  alternatives: {method: string; text: string; error?: string; warning?: string}[];
  evidence: string[]; checks: Check[];
  overlaps?: string[];
  fonts?: string[];
  font_size?: number;
  text_layout?: {bbox: [number, number, number, number]; font_size: number; line_height: number; align: "left" | "center"; bold: boolean; italic: boolean};
  form_review_note?: string; form_evidence?: {status: string; stored_value: unknown; appearance: {text?: string | null; present: boolean}; discrepancies: {code?: string; message?: string}[]};
  form_evidence_changed?: boolean;
  table_id?: string; cell_id?: string; row?: number; column?: number; row_span?: number; column_span?: number;
}
export interface TableProposal {
  id: string; status: string; row_count: number; column_count: number;
  bbox: number[]; method?: string; original_regions?: Region[]; original_proposal?: unknown;
  cells: {id: string; row: number; column: number; row_span: number; column_span: number; raw: string | null; bbox: number[]}[];
  warnings: {code: string; message: string}[]; structure_approved?: boolean;
  decision?: {action: string; note: string}; structure_review?: {note: string};
}
export interface ReviewPage {
  number: number; revision: number; width: number; height: number; status: string;
  regions: Region[]; coverage_approved: boolean; blockers: string[]; error: string;
  table_proposals?: {status: string; tables: TableProposal[]; warnings: {code: string; message: string}[]};
  structure_warning_review?: {note: string};
}
export interface ReviewJob {id: string; kind: string; status: string; cursor: number; message: string; payload: {pages: number[]}}
export interface ReviewExport {
  id: string; revision: number; format: string; mode: string; status: string; page_count: number;
  blockers: string[]; layout_approvals: Record<string, unknown>; current: boolean;
}
export interface Capabilities {
  translation: Record<string, {available: boolean; reason: string; model?: string; draft_only?: boolean}>;
  ocr: {installed: boolean}; structure: {reason: string; manual_tables?: boolean}; cloud: {available: boolean; reason: string};
}
