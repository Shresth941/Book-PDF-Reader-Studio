import { upload } from "@vercel/blob/client";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.PROD ? "" : "http://localhost:8000");
const USE_BLOB_STORAGE = import.meta.env.PROD && !import.meta.env.VITE_API_BASE_URL;

export interface DocumentInfo {
  id: string;
  filename: string;
  pageCount: number;
}

export interface ExtractedPage {
  page: number;
  text: string;
}

export interface ExtractedPagesResponse {
  pages: ExtractedPage[];
}

export interface TranslateResponse {
  text: string;
}

export interface DefinitionsResponse {
  definitions: string[];
}

export type ExportFormat = "txt" | "docx" | "pdf";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, init);
  } catch (error) {
    throw new Error(
      "Unable to connect to the PDF service. Please try again."
    );
  }

  if (!response.ok) {
    let detail = "Request failed.";
    try {
      const errorBody = await response.json();
      if (typeof errorBody.detail === "string") {
        detail = errorBody.detail;
      } else if (Array.isArray(errorBody.detail) && errorBody.detail[0]?.msg) {
        detail = errorBody.detail[0].msg;
      }
    } catch {
      detail = response.statusText || `HTTP Error ${response.status}`;
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

export const checkHealth = (): Promise<{ status: string }> => call("/api/health");

export const uploadPdf = async (file: File): Promise<DocumentInfo> => {
  if (USE_BLOB_STORAGE) {
    const blob = await upload(`documents/${crypto.randomUUID()}-${file.name}`, file, {
      access: "private",
      handleUploadUrl: "/api/blob-upload",
      multipart: file.size > 5 * 1024 * 1024,
    });
    return call<DocumentInfo>("/api/documents/blob", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blob_url: blob.url, filename: file.name }),
    });
  }
  const form = new FormData();
  form.append("file", file);
  return call<DocumentInfo>("/api/documents", {
    method: "POST",
    body: form,
  });
};

export const fetchPages = (
  id: string,
  start: number,
  end: number
): Promise<ExtractedPagesResponse> => {
  if (USE_BLOB_STORAGE) {
    return call<ExtractedPagesResponse>("/api/documents/blob/pages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blob_url: id, start, end }),
    });
  }
  return call<ExtractedPagesResponse>(`/api/documents/${id}/pages?start=${start}&end=${end}`);
};

export const translateText = (
  text: string,
  target: "en" | "hi" | string,
  source: string = "auto",
  documentTitle: string = ""
): Promise<TranslateResponse> =>
  call<TranslateResponse>("/api/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, source, target, document_title: documentTitle }),
  });

export const lookupDefinition = (
  term: string,
  language: "auto" | "en" | "hi" = "auto",
  context: string = "",
  documentTitle: string = "",
  documentId: string = ""
): Promise<DefinitionsResponse> =>
  call<DefinitionsResponse>("/api/definitions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      term,
      language,
      context,
      document_title: documentTitle,
      document_id: documentId,
    }),
  });

export const downloadDraft = async (text: string, format: ExportFormat): Promise<Blob> => {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/api/exports`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, format }),
    });
  } catch {
    throw new Error("Unable to prepare the download. Please make sure the backend server is running.");
  }

  if (!response.ok) {
    let detail = "Unable to prepare the download.";
    try {
      const errorBody = await response.json();
      if (typeof errorBody.detail === "string") detail = errorBody.detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  return response.blob();
};
