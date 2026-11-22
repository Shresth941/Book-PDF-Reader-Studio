/** Bounded browser smoke of actual offline Marian inference, not a quality benchmark. */
import {chromium} from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";

assert.ok(process.env.REVIEW_URL, "Set REVIEW_URL to a separate QA server with the real local models installed.");
const base = new URL(process.env.REVIEW_URL);
assert.ok(base.protocol === "http:" && ["127.0.0.1", "localhost"].includes(base.hostname)
  && base.port && base.port !== "8000", "Real-model QA requires a separate loopback port, never production 8000.");
const fixture = fileURLToPath(new URL("./fixtures/translation.pdf", import.meta.url));
const out = fileURLToPath(new URL("../../tmp/translation-browser-qa/", import.meta.url));
await fs.mkdir(out, {recursive: true});
const browser = await chromium.launch({channel: "chrome", headless: true});
const page = await browser.newPage({viewport: {width: 1500, height: 1050}});
page.setDefaultTimeout(20000);
const errors = [];
const externalRequests = [];
page.on("pageerror", error => errors.push(error.message));
await page.route("**/*", route => {
  const url = new URL(route.request().url());
  if (["http:", "https:"].includes(url.protocol) && url.origin !== base.origin) {
    externalRequests.push(url.origin);
    return route.abort();
  }
  return route.continue();
});
async function jsonGet(route) {
  const response = await page.request.get(new URL(`/api/v2${route}`, base).href);
  assert.ok(response.ok(), await response.text());
  return response.json();
}
async function mutation(button, suffix, method = "POST") {
  const responsePromise = page.waitForResponse(response => response.request().method() === method
    && new URL(response.url()).pathname.endsWith(suffix));
  await page.getByRole("button", {name: button, exact: true}).click();
  const response = await responsePromise;
  const data = await response.json();
  assert.ok(response.ok(), JSON.stringify(data));
  await page.waitForFunction(() => !document.querySelector('input[type="file"]')?.disabled);
  return data;
}
async function waitJob(documentId, jobId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let job;
  while (Date.now() < deadline) {
    job = (await jsonGet(`/documents/${documentId}/jobs`)).jobs.find(candidate => candidate.id === jobId);
    assert.ok(job, "Queued job disappeared from durable state.");
    if (job.status === "completed") return job;
    assert.ok(!["paused", "failed", "cancelled"].includes(job.status), JSON.stringify(job));
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  assert.fail(`Job exceeded its ${timeoutMs}ms browser smoke limit: ${JSON.stringify(job)}`);
}
try {
  await page.goto(base.href);
  await page.getByRole("button", {name: "Detailed review", exact: true}).click();
  await page.getByRole("heading", {name: "PDF Reader Studio", exact: true}).waitFor();
  await page.waitForFunction(() => !document.querySelector('input[type="file"]')?.disabled);
  const capabilities = await jsonGet("/capabilities");
  const resources = await jsonGet("/resources");
  assert.equal(capabilities.runtime, "local");
  assert.equal(capabilities.translation["en-hi"].available, true);
  assert.equal(capabilities.translation["en-hi"].provider, "marian-ct2");
  assert.equal(capabilities.translation["en-hi"].draft_only, true);
  assert.equal(capabilities.translation["en-hi"].quality_qualified, false);
  const importedPromise = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v2/documents");
  await page.locator('input[type="file"]').setInputFiles(fixture);
  const imported = await importedPromise;
  assert.ok(imported.ok(), await imported.text());
  const document = await imported.json();
  assert.equal(document.status, "ready", JSON.stringify(document.metadata));
  await page.getByText("Original saved. Review inspection status, then extract the pages.", {exact: true}).waitFor();
  const extract = await mutation("Extract page", "/jobs");
  await waitJob(document.id, extract.id, 120000);
  await page.getByText(/extract · completed/).waitFor();
  let savedPage = await jsonGet(`/documents/${document.id}/pages/1`);
  assert.equal(savedPage.regions.length, 1, "Smoke fixture must produce one short meaning unit.");
  const regionId = savedPage.regions[0].id;
  await page.getByLabel("Region", {exact: true}).selectOption(regionId);
  assert.equal(await page.getByLabel("Verified source", {exact: true}).inputValue(), "The debtor shall pay 100 rupees.");
  assert.equal(await page.getByRole("button", {name: "Generate page translations", exact: true}).isDisabled(), true);
  await page.getByLabel("Confirm source language", {exact: true}).selectOption("en");
  await mutation("Save changes", `/regions/${regionId}`, "PATCH");
  await mutation("Approve source recognition", "/approvals");
  const started = Date.now();
  const translate = await mutation("Generate page translations", "/jobs");
  const completed = await waitJob(document.id, translate.id, 240000);
  const translationMs = Date.now() - started;
  await page.getByText(/translate · completed/).waitFor();
  savedPage = await jsonGet(`/documents/${document.id}/pages/1`);
  const draft = savedPage.regions.find(region => region.id === regionId);
  assert.match(draft.translation, /[\u0900-\u097f]/, "Real model must return a Hindi draft.");
  assert.notEqual(draft.translation, draft.source);
  assert.equal(draft.source_approved, true);
  assert.equal(draft.translation_approved, false);
  assert.equal(draft.model, capabilities.translation["en-hi"].model);
  assert.equal(draft.draft_only, true);
  assert.equal(draft.quality_qualified, false);
  assert.equal(savedPage.coverage_approved, false);
  await page.waitForFunction(value => document.querySelector('textarea[aria-label="Translation"]')?.value === value, draft.translation);
  await page.getByText("The installed local model produces draft suggestions. Check the complete meaning, terminology, names and numbers before approving each translation.", {exact: true}).waitFor();
  await page.getByText("Review obligations, permissions, negation and exceptions in their full clause context.", {exact: true}).waitFor();
  await page.getByText("Verify who owes/receives each amount, which action/date it belongs to, and its unit. Matching numbers alone is insufficient.", {exact: true}).waitFor();
  assert.equal(await page.getByRole("button", {name: "✓ Translation reviewed", exact: true}).count(), 0);
  const exportResult = await mutation("Create review copy", "/exports");
  await page.getByRole("link", {name: "Download review copy", exact: true}).waitFor({timeout: 60000});
  assert.equal(await page.getByRole("link", {name: "Download reviewed PDF", exact: true}).count(), 0);
  assert.ok(exportResult.blockers.length, "An unreviewed model draft must have export blockers.");
  await page.waitForFunction(() => {
    const preview = document.querySelector(".export-preview");
    return preview instanceof HTMLImageElement && preview.complete && preview.naturalWidth > 0;
  }, undefined, {timeout: 60000});
  await page.screenshot({path: path.join(out, "real-model-draft.png"), fullPage: true});
  await page.reload();
  await page.locator(".library-item").filter({hasText: "translation.pdf"}).first().click();
  await page.getByLabel("Region", {exact: true}).selectOption(regionId);
  assert.equal(await page.getByLabel("Translation", {exact: true}).inputValue(), draft.translation);
  assert.equal(await page.getByRole("button", {name: "✓ Translation reviewed", exact: true}).count(), 0);
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  const report = {passed: true, scope: "Real local inference and review workflow smoke; not translation-quality qualification",
    model: capabilities.translation["en-hi"], limits: capabilities.limits, initial_resources: resources,
    document_id: document.id, translation_job: completed.id,
    translation_ms: translationMs, source: draft.source, draft: draft.translation,
    checks: ["actual Marian local provider available", "source review required before generation", "real Hindi draft from UI job",
      "model does not approve translation or page completeness", "legal scope and fact relationship warnings remain visible",
      "unreviewed draft blocks final download", "export preview decoded", "draft persists after reload", "no browser exceptions", "no external browser requests"],
    screenshots: out};
  await fs.writeFile(path.join(out, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (error) {
  await page.screenshot({path: path.join(out, "failure.png"), fullPage: true}).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
