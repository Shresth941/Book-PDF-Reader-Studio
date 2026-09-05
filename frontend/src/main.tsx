import React, { ChangeEvent, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  checkHealth,
  downloadDraft,
  DocumentInfo,
  ExportFormat,
  fetchPages,
  lookupDefinition,
  translateText,
  uploadPdf,
} from "./api";
import "./styles.css";

interface StatusMessage {
  type: "info" | "success" | "error" | "warning";
  text: string;
}

type Theme = "dark" | "light";

const FAQ_ITEMS = [
  {
    topic: "Getting started",
    question: "How do I read text from a PDF?",
    answer: "Choose a PDF, set the first and last page you want to read, then select Read Pages. The extracted text opens in the editable reading area.",
  },
  {
    topic: "Compare view",
    question: "Can I compare the original PDF with editable text?",
    answer: "Yes. Choose Compare after uploading a PDF to view the original page layout next to editable extracted text. Changes apply only to the draft, never to the original PDF.",
  },
  {
    topic: "Translation",
    question: "How do I translate English and Hindi text?",
    answer: "Open the Edit or Compare view, then use Hindi or English in the toolbar. Translation updates the editable draft and you can translate it back when needed.",
  },
  {
    topic: "Definitions",
    question: "How do I look up a word or legal term?",
    answer: "Highlight a Hindi or English word or short passage in the editable text, then choose Define selected. You can also type a word directly into Vocabulary & Definitions.",
  },
  {
    topic: "Downloads",
    question: "Can I download my edited text?",
    answer: "Yes. Choose TXT, Word, or PDF beside Save, then select Download. Your uploaded PDF always remains unchanged.",
  },
] as const;

const FAQ_STRUCTURED_DATA = JSON.stringify({
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: FAQ_ITEMS.map(({ question, answer }) => ({
    "@type": "Question",
    name: question,
    acceptedAnswer: {
      "@type": "Answer",
      text: answer,
    },
  })),
});

function getInitialTheme(): Theme {
  try {
    return window.localStorage.getItem("pdf-reader-theme") === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

interface SearchMatchRange {
  start: number;
  end: number;
}

interface SearchProjection {
  value: string;
  starts: number[];
  ends: number[];
}

const SEARCH_DASHES = new Set(["-", "‐", "‑", "‒", "–", "—", "―", "−"]);
const SEARCH_APOSTROPHES = new Set(["'", "‘", "’", "ʼ"]);

/**
 * Build a comparison string without losing the position of each source
 * character. PDF text often contains non-breaking spaces, copied zero-width
 * marks, or a hyphen followed by a line break. The projection makes those
 * forms searchable while its maps keep selection in the original editor exact.
 */
function createSearchProjection(value: string): SearchProjection {
  const segments = value.match(/\P{M}\p{M}*|\p{M}+/gu) ?? [];
  let sourceOffset = 0;
  let projected = "";
  const starts: number[] = [];
  const ends: number[] = [];
  let joinLineBreak = false;

  const append = (character: string, start: number, end: number) => {
    if (character === " " && projected.endsWith(" ")) {
      ends[ends.length - 1] = end;
      return;
    }
    projected += character;
    starts.push(start);
    ends.push(end);
  };

  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    const start = sourceOffset;
    sourceOffset += segment.length;
    const end = sourceOffset;
    const normalized = segment.normalize("NFC").toLocaleLowerCase();
    const nextSegment = segments[index + 1]?.normalize("NFC") ?? "";

    if (/^[\u200B-\u200D\uFEFF]+$/u.test(normalized)) continue;
    if (/^\s+$/u.test(normalized)) {
      if (joinLineBreak) {
        joinLineBreak = false;
      } else {
        append(" ", start, end);
      }
      continue;
    }
    if (SEARCH_DASHES.has(normalized) && /^\s+$/u.test(nextSegment)) {
      // A PDF line ending such as "adminis-\ntration" is one word.
      joinLineBreak = true;
      continue;
    }
    joinLineBreak = false;
    if (SEARCH_DASHES.has(normalized)) {
      append("-", start, end);
      continue;
    }
    if (SEARCH_APOSTROPHES.has(normalized)) {
      append("'", start, end);
      continue;
    }
    for (let characterIndex = 0; characterIndex < normalized.length; characterIndex += 1) {
      append(normalized[characterIndex], start, end);
    }
  }

  const firstCharacter = projected.search(/\S/u);
  if (firstCharacter < 0) return { value: "", starts: [], ends: [] };
  let lastCharacter = projected.length;
  while (lastCharacter > firstCharacter && /\s/u.test(projected[lastCharacter - 1])) lastCharacter -= 1;
  return {
    value: projected.slice(firstCharacter, lastCharacter),
    starts: starts.slice(firstCharacter, lastCharacter),
    ends: ends.slice(firstCharacter, lastCharacter),
  };
}

function findMatchRanges(text: string, query: string): SearchMatchRange[] {
  const queryProjection = createSearchProjection(query).value;
  if (!queryProjection) return [];

  const textProjection = createSearchProjection(text);
  const ranges: SearchMatchRange[] = [];
  let searchFrom = 0;
  while (searchFrom < textProjection.value.length) {
    const position = textProjection.value.indexOf(queryProjection, searchFrom);
    if (position < 0) break;
    const lastCharacter = position + queryProjection.length - 1;
    const start = textProjection.starts[position];
    const end = textProjection.ends[lastCharacter];
    if (start !== undefined && end !== undefined) ranges.push({ start, end });
    searchFrom = position + Math.max(queryProjection.length, 1);
  }
  return ranges;
}

function normalizeLookupTerm(value: string): string {
  return value
    .normalize("NFC")
    .replace(/[\u200B-\u200D\uFEFF]/gu, "")
    .replace(/\s+/gu, " ")
    .trim();
}

interface AppErrorBoundaryProps {
  children: React.ReactNode;
}

interface AppErrorBoundaryState {
  hasError: boolean;
}

class AppErrorBoundary extends React.Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="app-error-boundary" role="alert">
          <div className="app-error-card">
            <span className="app-error-icon" aria-hidden="true">📄</span>
            <h1>PDF Reader Studio needs a refresh</h1>
            <p>Your uploaded PDF is unchanged. Reload the page to continue safely.</p>
            <button className="btn btn-primary" type="button" onClick={() => window.location.reload()}>
              Reload app
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}

function App() {
  const [doc, setDoc] = useState<DocumentInfo | null>(null);
  const [startPage, setStartPage] = useState<number>(1);
  const [endPage, setEndPage] = useState<number>(1);
  const [draftText, setDraftText] = useState<string>("");
  const [status, setStatus] = useState<StatusMessage>({
    type: "info",
    text: "Welcome! Upload a PDF to start reading, translating, and exploring vocabulary.",
  });
  const [isBusy, setIsBusy] = useState<boolean>(false);
  const [busyAction, setBusyAction] = useState<string>("");
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [draftLanguage, setDraftLanguage] = useState<"auto" | "en" | "hi">("auto");
  const [downloadFormat, setDownloadFormat] = useState<ExportFormat>("txt");
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [viewMode, setViewMode] = useState<"text" | "pdf" | "compare">("text");
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  // Definition state
  const [definitions, setDefinitions] = useState<string[]>([]);
  const [activeWord, setActiveWord] = useState<string>("");
  const [activeContext, setActiveContext] = useState<string>("");
  const [directSearchTerm, setDirectSearchTerm] = useState<string>("");
  const [lastSelectedText, setLastSelectedText] = useState<string>("");
  const [hasActiveSelection, setHasActiveSelection] = useState<boolean>(false);
  const [findQuery, setFindQuery] = useState<string>("");
  const [findMatchIndex, setFindMatchIndex] = useState<number>(-1);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const findInputRef = useRef<HTMLInputElement | null>(null);
  // Use a ref (not state) to capture textarea selection synchronously before React re-renders
  const capturedSelectionRef = useRef<string>("");
  const findMatches = findMatchRanges(draftText, findQuery);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem("pdf-reader-theme", theme);
    } catch {
      // Theme still works for this session if browser storage is unavailable.
    }
  }, [theme]);

  useEffect(() => {
    const focusFind = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f" && (viewMode === "text" || viewMode === "compare")) {
        event.preventDefault();
        findInputRef.current?.focus();
        findInputRef.current?.select();
      }
    };
    window.addEventListener("keydown", focusFind);
    return () => window.removeEventListener("keydown", focusFind);
  }, [viewMode]);

  // Check backend health on mount
  useEffect(() => {
    checkHealth()
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false));
  }, []);

  const runAsync = async (actionName: string, fn: () => Promise<void>) => {
    setIsBusy(true);
    setBusyAction(actionName);
    try {
      await fn();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "An unexpected error occurred.";
      setStatus({ type: "error", text: message });
    } finally {
      setIsBusy(false);
      setBusyAction("");
    }
  };

  const handleFileUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setStatus({ type: "warning", text: "Please choose a valid .pdf file." });
      return;
    }

    runAsync("Uploading and analyzing PDF…", async () => {
      const docInfo = await uploadPdf(file);
      const nextPreviewUrl = URL.createObjectURL(file);
      setDoc(docInfo);
      setPreviewUrl(nextPreviewUrl);
      setViewMode("text");
      setStartPage(1);
      setEndPage(Math.min(docInfo.pageCount, 5));
      setDraftText("");
      setDraftLanguage("auto");
      setFindQuery("");
      setFindMatchIndex(-1);
      setHasActiveSelection(false);
      setDefinitions([]);
      setActiveWord("");
      setActiveContext("");
      setStatus({
        type: "success",
        text: `“${docInfo.filename}” uploaded successfully (${docInfo.pageCount} page${
          docInfo.pageCount > 1 ? "s" : ""
        }). Select a page range to read.`,
      });
    });
    // Reset file input so same file can be re-uploaded if needed
    e.target.value = "";
  };

  const handleReadPages = () => {
    if (!doc) return;

    const start = Number(startPage);
    const end = Number(endPage);

    if (
      !Number.isInteger(start) ||
      !Number.isInteger(end) ||
      start < 1 ||
      end < 1 ||
      start > doc.pageCount ||
      end > doc.pageCount
    ) {
      setStatus({
        type: "warning",
        text: `Please enter valid whole page numbers between 1 and ${doc.pageCount}.`,
      });
      return;
    }

    const first = Math.min(start, end);
    const last = Math.max(start, end);

    runAsync(`Extracting text from pages ${first}–${last}…`, async () => {
      // A hosted PDF lives in private Blob storage. Reading the selected range
      // in one request downloads that private file only once per operation.
      const response = await fetchPages(doc.id, first, last);
      const formattedDraft = response.pages
        .map(({ page, text }) => `── Page ${page} ──\n\n${text || "[No selectable text found on this page]"}`)
        .join("\n\n");
      setDraftText(formattedDraft);
      setDraftLanguage("auto");
      setFindMatchIndex(-1);
      setHasActiveSelection(false);
      setStatus({
        type: "success",
        text: `Extracted pages ${first} to ${last}. You can now edit, translate, or look up words in this draft.`,
      });
    });
  };

  const handleTranslate = (targetLang: "en" | "hi") => {
    if (!draftText.trim()) {
      setStatus({ type: "warning", text: "Draft is empty. Extract pages or type text before translating." });
      return;
    }
    const targetLabel = targetLang === "hi" ? "Hindi" : "English";
    runAsync(`Translating draft into ${targetLabel}…`, async () => {
      const res = await translateText(draftText, targetLang, draftLanguage, doc?.filename ?? "");
      setDraftText(res.text);
      setDraftLanguage(targetLang);
      setHasActiveSelection(false);
      setStatus({
        type: "success",
        text: `Successfully translated draft to ${targetLabel}.`,
      });
    });
  };

  const trackSelection = () => {
    if (textareaRef.current) {
      const { selectionStart, selectionEnd, value } = textareaRef.current;
      const selected = value.slice(selectionStart, selectionEnd).trim();
      if (selected) {
        // Write to ref immediately (synchronous) - avoids React state closure bug
        capturedSelectionRef.current = selected;
        setLastSelectedText(selected);
      }
      setHasActiveSelection(Boolean(selected));
    }
  };

  const documentContextFor = (term: string) => {
    const normalizedDraft = draftText.toLocaleLowerCase();
    const matchIndex = normalizedDraft.indexOf(term.toLocaleLowerCase());
    const contextStart = Math.max(0, matchIndex >= 0 ? matchIndex - 700 : 0);
    const contextEnd = Math.min(draftText.length, (matchIndex >= 0 ? matchIndex + term.length + 1100 : 1800));
    return draftText.slice(contextStart, contextEnd);
  };

  const handleLookup = (wordToLookup?: string) => {
    // Use the synchronously-captured ref value (avoids React stale closure bug)
    const rawWord = wordToLookup || capturedSelectionRef.current || lastSelectedText;
    const cleanWord = normalizeLookupTerm(rawWord).replace(/^[\p{P}\p{S}\s]+|[\p{P}\p{S}\s]+$/gu, "");

    if (!cleanWord) {
      setStatus({
        type: "warning",
        text: "Please select or type a Hindi or English word or phrase to look up its meaning.",
      });
      return;
    }
    const isPassage = cleanWord.split(/\s+/).length > 4;

    runAsync(`Looking up definition for “${cleanWord}”…`, async () => {
      setActiveWord(isPassage ? "Selected passage" : cleanWord);
      setActiveContext(isPassage ? cleanWord : "");
      const res = await lookupDefinition(
        cleanWord,
        "auto",
        documentContextFor(cleanWord),
        doc?.filename ?? "",
        doc?.id ?? "",
      );
      setDefinitions(res.definitions);
      if (res.definitions.length > 0) {
        setStatus({
          type: "success",
          text: isPassage
            ? `Found ${res.definitions.length} context-aware term${res.definitions.length > 1 ? "s" : ""} from the selected passage.`
            : `Found ${res.definitions.length} definition${res.definitions.length > 1 ? "s" : ""} for “${cleanWord}”.`,
        });
      } else {
        setStatus({
          type: "info",
          text: isPassage
            ? "No related terms were found. Try selecting a shorter phrase or one important word."
            : `No definitions found for “${cleanWord}”.`,
        });
      }
    });
  };

  const handleDirectSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const term = normalizeLookupTerm(directSearchTerm);
    if (term) {
      handleLookup(term);
      setDirectSearchTerm("");
    }
  };

  const handleCopyDraft = () => {
    if (!draftText) return;
    runAsync("Copying draft…", async () => {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Copying is not supported by this browser. Please select the text and copy it manually.");
      }
      await navigator.clipboard.writeText(draftText);
      setStatus({ type: "success", text: "Draft copied to clipboard!" });
    });
  };

  const handleFind = (direction: 1 | -1) => {
    const query = findQuery.trim();
    if (!query) {
      findInputRef.current?.focus();
      return;
    }
    if (!findMatches.length) {
      setFindMatchIndex(-1);
      setStatus({ type: "info", text: `No matches found for “${query}”.` });
      return;
    }

    const nextIndex = findMatchIndex < 0
      ? (direction === 1 ? 0 : findMatches.length - 1)
      : (findMatchIndex + direction + findMatches.length) % findMatches.length;
    const { start, end } = findMatches[nextIndex];
    const editor = textareaRef.current;
    if (editor) {
      editor.focus();
      editor.setSelectionRange(start, end);
      const selected = editor.value.slice(start, end);
      capturedSelectionRef.current = selected;
      setLastSelectedText(selected);
      setHasActiveSelection(true);
    }
    setFindMatchIndex(nextIndex);
    setStatus({ type: "info", text: `Match ${nextIndex + 1} of ${findMatches.length} for “${query}”.` });
  };

  const clearDraft = () => {
    setDraftText("");
    setFindQuery("");
    setFindMatchIndex(-1);
    setDefinitions([]);
    setActiveWord("");
    setActiveContext("");
    setLastSelectedText("");
    setHasActiveSelection(false);
    capturedSelectionRef.current = "";
  };

  const handleSelectAllDraft = () => {
    const editor = textareaRef.current;
    if (!editor || !editor.value) return;
    editor.focus();
    editor.select();
    const selected = editor.value.trim();
    capturedSelectionRef.current = selected;
    setLastSelectedText(selected);
    setHasActiveSelection(Boolean(selected));
  };

  const handleClearSelection = () => {
    const editor = textareaRef.current;
    if (editor) {
      editor.focus();
      editor.setSelectionRange(0, 0);
    }
    capturedSelectionRef.current = "";
    setLastSelectedText("");
    setHasActiveSelection(false);
  };

  const handleDownloadDraft = () => {
    if (!draftText) return;
    const label = downloadFormat === "docx" ? "Word document" : downloadFormat.toUpperCase();
    runAsync(`Preparing ${label} download…`, async () => {
      const blob = await downloadDraft(draftText, downloadFormat);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${doc ? doc.filename.replace(/\.pdf$/i, "") : "document"}_draft.${downloadFormat}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setStatus({ type: "success", text: `Draft downloaded as ${label}.` });
    });
  };

  const wordCount = draftText.trim() ? draftText.trim().split(/\s+/).length : 0;
  const charCount = draftText.length;
  const editorHeading = viewMode === "pdf"
    ? "Original PDF"
    : viewMode === "compare"
      ? "Compare PDF & Text"
      : "Edit Text";

  const renderDraftEditor = (className = "draft-textarea") => (
    <textarea
      ref={textareaRef}
      className={className}
      value={draftText}
      onChange={(e) => {
        setDraftText(e.target.value);
        setDraftLanguage("auto");
        setFindMatchIndex(-1);
        setHasActiveSelection(false);
      }}
      onSelect={trackSelection}
      onMouseUp={trackSelection}
      onKeyUp={trackSelection}
      placeholder={
        doc
          ? "Selected pages will appear here. You can freely edit, reformat, translate, and select words for definitions. Your original PDF is never modified."
          : "Upload a PDF above to extract readable pages into this interactive draft workbench."
      }
      spellCheck
    />
  );

  const renderPdfPreview = (className = "pdf-preview") => previewUrl ? (
    <iframe
      className={className}
      src={`${previewUrl}#page=${Math.max(1, startPage)}`}
      title={`Original uploaded PDF, starting at page ${Math.max(1, startPage)}`}
    />
  ) : null;

  return (
    <div className="app-container">
      {/* Top Navbar */}
      <header className="app-header">
        <div className="brand">
          <img className="brand-mark" src="/pdf-reader-icon.svg" alt="" />
          <h1>PDF Reader Studio</h1>
        </div>
        <div className="header-badges">
          <span className={`status-pill ${backendOnline ? "online" : "offline"}`}>
            <span className="dot"></span> {backendOnline ? "Online" : "Connecting..."}
          </span>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            <span aria-hidden="true">{theme === "dark" ? "☀️" : "🌙"}</span>
          </button>
        </div>
      </header>

      {/* Main Container */}
      <main className="main-content">
        {/* Top Control Grid */}
        <section className="card control-card">
          <div className="control-grid">
            {/* Upload Area */}
            <div className="upload-box">
              <label className={`upload-btn ${isBusy ? "disabled" : ""}`}>
                <input
                  type="file"
                  accept="application/pdf"
                  onChange={handleFileUpload}
                  disabled={isBusy}
                />
                <span className="icon">📁</span>
                <span>{doc ? "Change PDF" : "Choose / Upload PDF"}</span>
              </label>
              {doc && (
                <div className="doc-chip">
                  <span className="doc-name" title={doc.filename}>{doc.filename}</span>
                  <span className="doc-badge">{doc.pageCount} page{doc.pageCount > 1 ? "s" : ""}</span>
                </div>
              )}
            </div>

            {/* Page Range Controls */}
            {doc && (
              <div className="range-controls">
                <div className="range-inputs">
                  <label className="input-group">
                    <span>From Page</span>
                    <input
                      type="number"
                      min={1}
                      max={doc.pageCount}
                      value={startPage || ""}
                      onChange={(e) => {
                        const val = parseInt(e.target.value, 10);
                        setStartPage(isNaN(val) ? 1 : Math.max(1, Math.min(val, doc.pageCount)));
                      }}
                      disabled={isBusy}
                    />
                  </label>
                  <span className="range-separator">to</span>
                  <label className="input-group">
                    <span>To Page</span>
                    <input
                      type="number"
                      min={1}
                      max={doc.pageCount}
                      value={endPage || ""}
                      onChange={(e) => {
                        const val = parseInt(e.target.value, 10);
                        setEndPage(isNaN(val) ? 1 : Math.max(1, Math.min(val, doc.pageCount)));
                      }}
                      disabled={isBusy}
                    />
                  </label>
                  <button
                    className="btn btn-primary read-btn"
                    onClick={handleReadPages}
                    disabled={isBusy}
                  >
                    {isBusy && busyAction.includes("Extracting") ? "Reading..." : "Read Pages"}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Status Feedback Banner */}
          <div className={`status-banner banner-${status.type}`}>
            {isBusy && <span className="spinner"></span>}
            <span className="status-text">{isBusy ? busyAction : status.text}</span>
          </div>
        </section>

        {/* Dual Pane Workspace */}
        <div className={`workspace-grid ${viewMode === "compare" ? "is-comparing" : ""}`}>
          {/* Left Pane: Draft Editor */}
          <section className="card editor-card">
            <div className="card-header">
              <div className="header-title">
                <h2>{editorHeading}</h2>
                <span className="stats-badge">
                  {wordCount} words · {charCount} chars
                </span>
              </div>
              <div className="view-switcher" role="group" aria-label="Reading view">
                <button
                  type="button"
                  className={`view-mode-btn ${viewMode === "text" ? "is-active" : ""}`}
                  onClick={() => setViewMode("text")}
                  aria-pressed={viewMode === "text"}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className={`view-mode-btn ${viewMode === "compare" ? "is-active" : ""}`}
                  onClick={() => setViewMode("compare")}
                  disabled={!previewUrl}
                  aria-pressed={viewMode === "compare"}
                  title="Compare the original PDF beside the editable text"
                >
                  Compare
                </button>
                <button
                  type="button"
                  className={`view-mode-btn ${viewMode === "pdf" ? "is-active" : ""}`}
                  onClick={() => setViewMode("pdf")}
                  disabled={!previewUrl}
                  aria-pressed={viewMode === "pdf"}
                  title="Show the original PDF"
                >
                  PDF
                </button>
              </div>
            </div>

            <div className="editor-toolbar" aria-label="Text editing tools">
              <div className="tool-group">
                <div className="find-bar" role="search" aria-label="Text finder controls">
                  <input
                    ref={findInputRef}
                    className="find-input"
                    type="search"
                    value={findQuery}
                    onChange={(event) => {
                      setFindQuery(event.target.value);
                      setFindMatchIndex(-1);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        handleFind(event.shiftKey ? -1 : 1);
                      }
                    }}
                    placeholder="Find in text"
                    disabled={isBusy || !draftText.trim()}
                    aria-label="Find in extracted text"
                  />
                  <button type="button" className="find-nav-btn" onClick={() => handleFind(-1)} disabled={isBusy || !findQuery.trim()} aria-label="Previous match" title="Previous match">↑</button>
                  <button type="button" className="find-nav-btn" onClick={() => handleFind(1)} disabled={isBusy || !findQuery.trim()} aria-label="Next match" title="Next match">↓</button>
                  <span className="find-count" aria-live="polite">
                    {findQuery.trim() ? (findMatches.length ? `${findMatchIndex >= 0 ? findMatchIndex + 1 : 0}/${findMatches.length}` : "0 matches") : "Find"}
                  </span>
                </div>
              </div>

              <div className="tool-group">
                <button type="button" className="btn btn-tool" onClick={() => handleTranslate("hi")} disabled={isBusy || !draftText.trim()} title="Translate entire draft to Hindi">🌐 Hindi</button>
                <button type="button" className="btn btn-tool" onClick={() => handleTranslate("en")} disabled={isBusy || !draftText.trim()} title="Translate entire draft to English">🌐 English</button>
              </div>

              <div className="tool-group tool-group-actions">
                <button
                  type="button"
                  className="btn btn-tool btn-lookup"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    if (textareaRef.current) {
                      const { selectionStart, selectionEnd, value } = textareaRef.current;
                      const sel = value.slice(selectionStart, selectionEnd).trim();
                      capturedSelectionRef.current = sel || capturedSelectionRef.current;
                    }
                  }}
                  onClick={() => handleLookup()}
                  disabled={isBusy || !draftText.trim()}
                  title="Highlight a word in the text then click to look up"
                >
                  Define selected
                </button>
                <button type="button" className="btn btn-tool" onClick={handleCopyDraft} disabled={!draftText.trim()} title="Copy draft to clipboard">Copy</button>
                <div className="download-control">
                  <label className="download-format" htmlFor="download-format" title="Choose a download format">
                    <span className="download-format-label">Save as</span>
                    <span className="download-select-wrap">
                      <select id="download-format" value={downloadFormat} onChange={(event) => setDownloadFormat(event.target.value as ExportFormat)} disabled={isBusy || !draftText.trim()}>
                        <option value="txt">Text (.txt)</option>
                        <option value="docx">Word (.docx)</option>
                        <option value="pdf">PDF (.pdf)</option>
                      </select>
                      <span className="download-select-chevron" aria-hidden="true" />
                    </span>
                  </label>
                  <button
                    type="button"
                    className="btn btn-download"
                    onClick={handleDownloadDraft}
                    disabled={isBusy || !draftText.trim()}
                    title={`Download draft as ${downloadFormat === "docx" ? "Word" : downloadFormat.toUpperCase()}`}
                  >
                    Download
                  </button>
                </div>
                <button type="button" className="btn btn-tool btn-danger" onClick={clearDraft} disabled={!draftText.trim()} title="Clear draft">Clear</button>
              </div>
            </div>

            <div className="editor-wrapper">
              {viewMode === "pdf" ? renderPdfPreview() : viewMode === "compare" ? (
                <div className="compare-view" aria-label="PDF and editable text comparison view">
                  <div className="compare-pane">
                    <div className="compare-pane-header">
                      <div className="compare-pane-title">
                        <span className="compare-pane-kicker">Original</span>
                        <strong>PDF layout</strong>
                      </div>
                      <span>Starts at page {startPage}</span>
                    </div>
                    {renderPdfPreview("pdf-preview compare-pdf-preview")}
                  </div>
                  <div className="compare-pane">
                    <div className="compare-pane-header compare-editor-header">
                      <div className="compare-pane-title">
                        <span className="compare-pane-kicker">Editable</span>
                        <strong>Extracted text</strong>
                        <span>Pages {startPage}–{endPage}</span>
                      </div>
                      <div className="compare-selection-actions" aria-label="Text selection controls">
                        <span className={`selection-state ${hasActiveSelection ? "has-selection" : ""}`}>
                          {hasActiveSelection ? "Text selected" : "Select text"}
                        </span>
                        <button
                          type="button"
                          className="compare-select-btn"
                          onClick={handleSelectAllDraft}
                          disabled={!draftText.trim()}
                          title="Select all editable text"
                        >
                          Select all
                        </button>
                        <button
                          type="button"
                          className="compare-select-btn"
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={handleClearSelection}
                          disabled={!hasActiveSelection}
                          title="Deselect text"
                        >
                          Deselect
                        </button>
                      </div>
                    </div>
                    {renderDraftEditor("draft-textarea compare-draft-textarea")}
                  </div>
                </div>
              ) : renderDraftEditor()}
            </div>
            {viewMode !== "pdf" && (
              <p className="reader-guide">
                {viewMode === "compare"
                  ? "Compare the original PDF on the left with the editable draft on the right. The original PDF is never changed."
                  : "The original PDF is unchanged. Select a word or passage to get a definition or legal-term hint."}
              </p>
            )}
          </section>

          {/* Right Pane: Dictionary & Vocabulary */}
          <aside className="card dictionary-card">
            <div className="card-header">
              <h2>Vocabulary & Definitions</h2>
            </div>

            {/* Direct Word Search */}
            <form className="dict-search-form" onSubmit={handleDirectSearch}>
              <input
                type="text"
                className="dict-search-input"
                placeholder="Type or select a Hindi or English word..."
                value={directSearchTerm}
                onChange={(e) => setDirectSearchTerm(e.target.value)}
                disabled={isBusy}
              />
              <button
                type="submit"
                className="btn btn-primary btn-sm"
                disabled={isBusy || !directSearchTerm.trim()}
              >
                Search
              </button>
            </form>

            {/* Definitions Output */}
            <div className="definitions-container">
              {activeWord && (
                <div className="active-word-header">
                  <span className="word-label">Term:</span>
                  <span className="word-term" title={activeContext || activeWord}>{activeWord}</span>
                  {definitions.length > 0 && (
                    <span className="def-count-badge">
                      {definitions.length} definition{definitions.length > 1 ? "s" : ""}
                    </span>
                  )}
                </div>
              )}

              {activeContext && (
                <p className="context-query" title={activeContext}>
                  Context: {activeContext}
                </p>
              )}

              {definitions.length > 0 ? (
                <ul className="definitions-list">
                  {definitions.map((def, idx) => {
                    const labelledDefinition = def.match(/^(English|Hindi):\s*(.*)$/);
                    const language = labelledDefinition?.[1];
                    const meaning = labelledDefinition?.[2] || def;
                    return (
                      <li key={idx} className="definition-item">
                        <span className="def-number">{idx + 1}</span>
                        <span className="def-content">
                          {language && <span className={`definition-language definition-language-${language.toLowerCase()}`}>{language}</span>}
                          <span>{meaning}</span>
                        </span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <div className="empty-dict-state">
                  <div className="empty-icon">📖</div>
                  <p className="empty-title">
                    {activeWord
                      ? `No definitions found for “${activeWord}”.`
                      : "Instant Word Lookup"}
                  </p>
                  <p className="empty-desc">
                    {activeWord === "Selected passage"
                      ? "Try selecting one important word or a shorter phrase from this passage."
                      : <>Highlight any Hindi or English word or passage in the editor and click <strong>“Define Selected”</strong>, or type directly in the search bar above.</>}
                  </p>
                </div>
              )}
            </div>
          </aside>
        </div>
      </main>
      <section className="faq-section" id="faq" aria-labelledby="faq-heading">
        <div className="faq-intro">
          <span className="faq-eyebrow">Help centre</span>
          <h2 id="faq-heading">Need a hand?</h2>
          <p>Everything you need to read, compare, translate, and export a PDF with confidence.</p>
          <ol className="faq-steps" aria-label="Getting started">
            <li><span>1</span><div><strong>Upload</strong><small>Choose your PDF file.</small></div></li>
            <li><span>2</span><div><strong>Read</strong><small>Set the pages and select Read Pages.</small></div></li>
            <li><span>3</span><div><strong>Work</strong><small>Edit, compare, translate, or export.</small></div></li>
          </ol>
        </div>
        <div className="faq-content">
          <div className="faq-content-header">
            <div>
              <span className="faq-kicker">Popular help topics</span>
              <h3>Quick answers</h3>
            </div>
            <span className="faq-count">{FAQ_ITEMS.length} guides</span>
          </div>
          <div className="faq-list">
          {FAQ_ITEMS.map(({ topic, question, answer }, index) => (
            <details key={question} className="faq-item">
              <summary>
                <span className="faq-number">{String(index + 1).padStart(2, "0")}</span>
                <span className="faq-summary-copy">
                  <span className="faq-topic">{topic}</span>
                  <span>{question}</span>
                </span>
              </summary>
              <p>{answer}</p>
            </details>
          ))}
          </div>
        </div>
      </section>
      <footer className="app-footer">
        <div className="footer-brand">
          <strong>PDF Reader Studio</strong>
          <span>Read, compare, and refine PDF text</span>
        </div>
        <nav className="footer-links" aria-label="Footer navigation">
          <a href="#faq">Help &amp; FAQ</a>
          <span>Original PDF stays unchanged</span>
        </nav>
      </footer>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: FAQ_STRUCTURED_DATA }} />
    </div>
  );
}

const rootElement = document.getElementById("root");
if (rootElement) {
  createRoot(rootElement).render(
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>,
  );
}
