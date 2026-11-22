import type {Region} from "./reviewApi";

// Browser drafts are untrusted; restore editable fields only, never evidence or approvals.
export function restoreRegionDraft(current: Region, saved: unknown): Region {
  if (!saved || typeof saved !== "object") throw new Error("Invalid region draft");
  const data = saved as Record<string, unknown>;
  const restored = {...current};
  for (const key of ["source", "translation", "reason", "form_review_note"] as const) {
    if (typeof data[key] === "string") restored[key] = data[key];
  }
  for (const [key, allowed] of [
    ["kind", ["paragraph", "heading", "list", "footnote", "caption", "formula", "image", "manual", "page", "table_cell"]],
    ["source_language", ["uncertain", "en", "hi", "mixed"]],
    ["disposition", ["translate", "already_target", "preserve", "decorative", "unreadable", "excluded"]],
  ] as const) {
    if (typeof data[key] === "string" && (allowed as readonly string[]).includes(data[key])) restored[key] = data[key];
  }
  if (typeof data.contains_text === "boolean") restored.contains_text = data.contains_text;
  return restored;
}
