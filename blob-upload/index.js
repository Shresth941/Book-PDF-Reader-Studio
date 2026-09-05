import express from "express";
import { handleUpload } from "@vercel/blob/client";

const app = express();
const MAX_PDF_BYTES = 25 * 1024 * 1024;

app.use(express.json({ limit: "64kb" }));

app.post("/api/blob-upload", async (request, response) => {
  try {
    const result = await handleUpload({
      body: request.body,
      request,
      token: process.env.BLOB_READ_WRITE_TOKEN,
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
    response.json(result);
  } catch (error) {
    response.status(400).json({
      error: error instanceof Error ? error.message : "Unable to authorize the PDF upload.",
    });
  }
});

export default app;
