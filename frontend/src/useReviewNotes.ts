import {useEffect, useState} from "react";

type Notes = Record<string, string>;
type Draft = {key: string; revision: number; value: Notes; dirty: boolean; storageFailed: boolean};

// Notes have their own draft lifecycle: typing a justification must not invalidate
// content, but switching pages or a background refresh must not discard it either.
export function useReviewNotes(key: string, revision: number, approved: Notes) {
  const serialized = JSON.stringify(approved);
  const baseline = (): Draft => ({key, revision, value: approved, dirty: false, storageFailed: false});
  const [draft, setDraft] = useState<Draft>(baseline);
  useEffect(() => {
    let next = baseline();
    if (key) try {
      const raw = localStorage.getItem(key);
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved && Number.isInteger(saved.revision) && saved.value && typeof saved.value === "object"
          && !Array.isArray(saved.value) && Object.values(saved.value).every(v => typeof v === "string")) {
          next = {...next, revision: saved.revision, value: saved.value, dirty: true};
        }
      }
    } catch { /* Existing in-memory notes remain usable if storage is blocked. */ }
    setDraft(previous => previous.key === key && previous.dirty && previous.storageFailed ? previous : next);
  }, [key, revision, serialized]);
  const current = draft.key === key ? draft : baseline();
  function edit(value: Notes) {
    let storageFailed = false;
    try { localStorage.setItem(key, JSON.stringify({revision, value})); }
    catch { storageFailed = true; }
    setDraft({key, revision, value, dirty: true, storageFailed});
  }
  function clear() {
    try { localStorage.removeItem(key); }
    catch { setDraft({...current, storageFailed: true}); return; }
    setDraft(baseline());
  }
  return {...current, stale: current.dirty && current.revision !== revision, edit, clear};
}
