const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

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
      "Unable to connect to the backend server. Please make sure the FastAPI server is running on port 8000."
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

export const checkHealth = (): Promise<{ status: string }> => call("/health");

export const uploadPdf = (file: File): Promise<DocumentInfo> => {
  const form = new FormData();
  form.append("file", file);
  return call<DocumentInfo>("/documents", {
    method: "POST",
    body: form,
  });
};

export const fetchPages = (
  id: string,
  start: number,
  end: number
): Promise<ExtractedPagesResponse> =>
  call<ExtractedPagesResponse>(`/documents/${id}/pages?start=${start}&end=${end}`);

export const translateText = (
  text: string,
  target: "en" | "hi" | string,
  source: string = "auto",
  documentTitle: string = ""
): Promise<TranslateResponse> =>
  call<TranslateResponse>("/translate", {
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
  call<DefinitionsResponse>("/definitions", {
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
    response = await fetch(`${BASE_URL}/exports`, {
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
