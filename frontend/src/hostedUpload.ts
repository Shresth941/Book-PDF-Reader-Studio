import {reviewCall, ReviewDocument} from "./reviewApi";

export async function hostedUpload(file: File): Promise<ReviewDocument> {
  if (file.size > 64 * 1024 * 1024) throw new Error("The Vercel workspace accepts PDFs up to 64 MB. Use the local workspace for larger files.");
  const authorization = await reviewCall<{url: string; headers: Record<string, string>; ticket: string}>("/uploads", "POST", {filename: file.name, size: file.size});
  const upload = await fetch(authorization.url, {method: "PUT", headers: authorization.headers, body: file, credentials: "omit"});
  if (!upload.ok) throw new Error("Private storage could not accept this upload. Check the free storage allowance and retry.");
  return reviewCall<ReviewDocument>("/uploads/complete", "POST", {ticket: authorization.ticket});
}
