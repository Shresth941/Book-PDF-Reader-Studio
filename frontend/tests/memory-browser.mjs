/** UI-only synthetic RAM snapshots; backend recovery is covered separately. */
import {chromium} from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import {fileURLToPath} from "node:url";

assert.ok(process.env.REVIEW_URL, "Set REVIEW_URL to a separate QA server.");
const base = new URL(process.env.REVIEW_URL);
assert.ok(base.protocol === "http:" && ["localhost", "127.0.0.1"].includes(base.hostname)
  && base.port && base.port !== "8000", "Never run this fixture on the user's port8000.");
const browser = await chromium.launch({channel: "chrome", headless: true});
const page = await browser.newPage({viewport: {width: 1000, height: 1000}});
const document = {id: "00000000-0000-0000-0000-000000000001", filename: "memory-pause.pdf",
  page_count: 0, revision: 1, status: "needs_attention", target: "hi", domain: "general", jurisdiction: "",
  review_status: "needs_attention", metadata: {code: "low_memory", message: "Available RAM is 600 MB; at least 1024 MB is required to start processing."},
  progress: {extracted: 0, coverage_approved: 0, content_approved: 0}};
let available = 600;
const errors = [];
page.on("pageerror", error => errors.push(error.message));
await page.route("**/api/v2/**", async route => {
  const path = new URL(route.request().url()).pathname.replace("/api/v2", "");
  const fixtures = {"/documents": {documents: [document]},
    [`/documents/${document.id}`]: document,
    [`/documents/${document.id}/jobs`]: {jobs: []},
    [`/documents/${document.id}/exports`]: {exports: []},
    [`/documents/${document.id}/glossary`]: {entries: []},
    "/resources": {memory: {available_mb: available, total_mb: 8192, required_mb: 1024, can_start_worker: available >= 1024}}};
  if (fixtures[path]) return route.fulfill({json: fixtures[path]});
  return route.continue();
});
try {
  await page.goto(base.href);
  await page.getByRole("button", {name: "Detailed review", exact: true}).click();
  await page.getByRole("button", {name: /memory-pause.pdf/}).click();
  await page.getByText(/Available RAM now: 600 MB/).waitFor();
  assert.ok(await page.getByText(/Page count pending inspection/).count() >= 2);
  assert.equal(await page.getByText(/\b0 pages\b/).count(), 0);
  await page.getByText(/This is computer memory, not disk space/).waitFor();
  assert.equal(await page.getByRole("button", {name: "Retry inspection / unlock", exact: true}).isEnabled(), true);
  available = 2048;
  await page.getByRole("button", {name: "Check available RAM", exact: true}).click();
  await page.getByText(/Available RAM now: 2,048 MB/).waitFor();
  await page.getByText(/The memory check currently passes/).waitFor();
  assert.deepEqual(errors, []);
  const out = fileURLToPath(new URL("../../tmp/memory-browser-qa/", import.meta.url));
  await fs.mkdir(out, {recursive: true});
  await page.screenshot({path: out + "memory-recovered.png", fullPage: true});
  const report = {passed: true, scope: "Mocked RAM snapshots exercise the real frontend; no user records modified",
    checks: ["unknown page count is explicit", "current and required RAM visible", "memory vs disk guidance",
      "retry remains available", "refresh shows recovered memory", "no browser exceptions"]};
  await fs.writeFile(out + "report.json", JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally { await browser.close(); }
