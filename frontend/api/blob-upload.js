import { handleUpload } from "@vercel/blob/client";

const MAX_PDF_BYTES = 25 * 1024 * 1024;

export async function POST(request) {
  try {
    const body = await request.json();
    const response = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname) => {
        if (!pathname.startsWith("documents/") || !pathname.toLowerCase().endsWith(".pdf")) {
          throw new Error("Only PDF documents are accepted.");
        }
        return {
          allowedContentTypes: ["application/pdf"],
          maximumSizeInBytes: MAX_PDF_BYTES,
          addRandomSuffix: true,
          validUntil: Date.now() + 10 * 60 * 1000,
        };
      },
      onUploadCompleted: async () => {},
    });
    return Response.json(response);
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Unable to authorize the PDF upload." },
      { status: 400 },
    );
  }
}
