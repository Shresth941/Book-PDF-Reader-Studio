/** Real simple-reader workflow. Use a separate local QA database and installed models. */
import {chromium} from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import {fileURLToPath} from "node:url";

const base = new URL(process.env.REVIEW_URL || "http://127.0.0.1:8002");
assert.ok(["127.0.0.1", "localhost"].includes(base.hostname) && base.port && base.port !== "8000", "Use an isolated QA server, not port 8000.");
const browser = await chromium.launch({channel: "chrome", headless: true});
const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
page.setDefaultTimeout(30000);
const out = fileURLToPath(new URL("../../tmp/reader-browser-qa/", import.meta.url));
await fs.mkdir(out, {recursive: true});
const errors = [], external = [], checks = [];
page.on("pageerror", e => errors.push(e.message));
await page.route("**/*", route => {
  const url = new URL(route.request().url());
  if (["http:", "https:"].includes(url.protocol) && url.origin !== base.origin) {external.push(url.href); return route.abort();}
  return route.continue();
});
const name = `reader-check-${Date.now()}.pdf`;
const fixture = await fs.readFile(new URL("./fixtures/reader.pdf", import.meta.url));
async function idle() {
  await page.waitForFunction(() => !document.querySelector('input[aria-label="Open PDF"]')?.disabled, undefined, {timeout: 120000});
}
async function get(path) {
  const r = await page.request.get(new URL(`/api/v2${path}`, base).href);
  assert.ok(r.ok(), await r.text()); return r.json();
}
try {
  await page.goto(base.href);
  await page.getByRole("heading", {name: "Your PDF, easier to read."}).waitFor();
  await idle();
  await page.getByLabel("Open PDF", {exact: true}).setInputFiles({name: "not-a-pdf.txt", mimeType: "text/plain", buffer: Buffer.from("test")});
  await page.getByRole("alert").filter({hasText: "Choose a PDF"}).waitFor();
  checks.push("unsupported extension explained");
  const importedPromise = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v2/documents");
  await page.getByLabel("Open PDF", {exact: true}).setInputFiles({name, mimeType: "application/pdf", buffer: fixture});
  const imported = await importedPromise;
  assert.equal(imported.status(), 201);
  const doc = await imported.json();
  await page.getByLabel("Extracted text 1", {exact: true}).waitFor({timeout: 120000});
  await idle();
  assert.match(await page.getByLabel("Extracted text 1", {exact: true}).inputValue(), /This is a book/);
  assert.equal(await page.getByRole("button", {name: "Translate page", exact: true}).isDisabled(), true);
  const original = await page.request.get(new URL(`/api/v2/documents/${doc.id}/original`, base).href);
  assert.deepEqual(await original.body(), fixture);
  checks.push("upload automatically extracts first page", "original bytes preserved", "translation requires explicit source check");
  const textDownload = page.waitForEvent("download");
  await page.getByRole("button", {name: "Download this page’s extracted text", exact: true}).click();
  const downloaded = await textDownload;
  assert.match(await fs.readFile(await downloaded.path(), "utf8"), /This is a book/);
  checks.push("extracted page text downloads without translation");

  await page.getByRole("button", {name: "Next page", exact: true}).click();
  await page.getByRole("heading", {name: "Bring the text into view"}).waitFor();
  await idle();
  await page.getByRole("button", {name: "Read all pages", exact: true}).click();
  await page.getByLabel("Extracted text 1", {exact: true}).waitFor({timeout: 120000});
  await idle();
  assert.match(await page.getByLabel("Extracted text 1", {exact: true}).inputValue(), /second page/);
  assert.equal((await get(`/documents/${doc.id}`)).progress.extracted, 2);
  checks.push("page navigation and remaining-document extraction");
  await page.getByRole("button", {name: "Previous page", exact: true}).click();
  await idle();
  await page.getByLabel("Extracted text 1", {exact: true}).fill("This is a useful book.");
  assert.equal(await page.getByRole("button", {name: "Next page", exact: true}).isDisabled(), true);
  assert.equal(await page.getByRole("button", {name: "Detailed review", exact: true}).isDisabled(), true);
  assert.equal(await page.getByRole("button", {name: "Prepare download", exact: true}).isDisabled(), true);
  page.once("dialog", d => d.accept());
  await page.reload();
  await page.getByRole("button", {name: new RegExp(name)}).click();
  await page.getByText("Recovered your unsaved changes. Compare them with the original, then save.", {exact: true}).waitFor();
  assert.equal(await page.getByLabel("Extracted text 1", {exact: true}).inputValue(), "This is a useful book.");
  await page.getByRole("button", {name: "Save changes", exact: true}).click();
  await page.getByText("Changes saved. Your original PDF is unchanged.", {exact: true}).waitFor();
  assert.equal((await get(`/documents/${doc.id}/pages/1`)).regions[0].source, "This is a useful book.");
  checks.push("unsaved navigation guards", "draft recovery after reload", "save persistence");

  await page.getByLabel("Translation language", {exact: true}).selectOption("en");
  await page.getByText("Choose a different language to translate into.", {exact: true}).waitFor();
  await page.getByLabel("Translation language", {exact: true}).selectOption("hi");
  await page.getByRole("checkbox", {name: /I checked the extracted text/}).check();
  await page.getByRole("button", {name: "Translate page", exact: true}).click();
  await page.waitForFunction(() => {
    const t = document.querySelector('textarea[aria-label="Translation 1"]');
    return t && t.value.trim() && !document.querySelector('input[aria-label="Open PDF"]')?.disabled;
  }, undefined, {timeout: 180000});
  const translated = await get(`/documents/${doc.id}/pages/1`);
  assert.ok(translated.regions.every(r => r.source_approved && r.translation.trim() && !r.translation_approved));
  assert.equal(translated.coverage_approved, false);
  checks.push("same-language guidance", "real offline page translation", "no automatic translation or completeness approval");
  await page.getByRole("button", {name: "Compare text", exact: true}).click();
  assert.equal(await page.getByLabel("Extracted text 1", {exact: true}).isVisible(), true);
  assert.equal(await page.getByLabel("Translation 1", {exact: true}).isVisible(), true);
  await page.screenshot({path: out + "desktop.png", fullPage: true});
  await page.getByLabel("Page zoom", {exact: true}).selectOption("150");
  await page.getByRole("button", {name: "Hide original", exact: true}).click();
  assert.equal(await page.getByRole("img", {name: "Original PDF page 1"}).count(), 0);
  await page.getByRole("button", {name: "Show original", exact: true}).click();
  checks.push("comparison view, original visibility and zoom");

  for (const format of ["pdf", "docx", "txt"]) {
    await page.getByLabel("Download format", {exact: true}).selectOption(format);
    await page.getByRole("button", {name: "Prepare download", exact: true}).click();
    const link = page.getByRole("link", {name: `Download ${format.toUpperCase()} review copy`, exact: true});
    await link.waitFor({timeout: 120000});
    const url = new URL(await link.getAttribute("href"), base);
    const download = await page.request.get(url.href);
    assert.ok(download.ok()); assert.ok((await download.body()).length > 20);
    assert.equal((await page.request.get(url.href + "?final=true")).status(), 409);
  }
  checks.push("PDF, Word and text downloads", "unreviewed final export remains blocked");
  await page.getByRole("button", {name: "Detailed review", exact: true}).click();
  await page.getByRole("heading", {name, exact: true}).waitFor();
  await page.getByRole("button", {name: "Back to simple reader", exact: true}).click();
  await page.getByRole("heading", {name, exact: true}).waitFor();
  await page.getByLabel("Extracted text 1", {exact: true}).waitFor();
  checks.push("detailed-review handoff keeps the selected document");

  // A failed save keeps the user's draft and an actionable message.
  await page.getByLabel("Extracted text 1", {exact: true}).fill("Unsaved conflict recovery");
  await page.route("**/regions/*", route => route.request().method() === "PATCH" ? route.fulfill({status: 409, json: {detail: "This content changed. Reload before saving; your edit has not been applied."}}) : route.continue());
  await page.getByRole("button", {name: "Save changes", exact: true}).click();
  await page.getByRole("alert").filter({hasText: "This content changed"}).waitFor();
  assert.equal(await page.getByLabel("Extracted text 1", {exact: true}).inputValue(), "Unsaved conflict recovery");
  await page.unroute("**/regions/*");
  page.once("dialog", d => d.accept());
  await page.getByRole("button", {name: "Discard changes", exact: true}).click();
  checks.push("failed save preserves draft");

  // Reload capabilities with a synthetic missing-model response; no network fallback.
  await page.route("**/capabilities", async route => {
    const response = await route.fetch(); const body = await response.json();
    for (const value of Object.values(body.translation)) {value.available = false; value.reason = "Model intentionally absent in this test.";}
    await route.fulfill({json: body});
  });
  await page.reload();
  await page.getByRole("button", {name: new RegExp(name)}).click();
  await page.getByText(/Automatic translation is unavailable/).waitFor();
  assert.equal(await page.getByRole("button", {name: "Translate page again", exact: true}).isDisabled(), true);
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: out + "mobile.png", fullPage: true});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  checks.push("missing model explained without external fallback", "390px mobile layout");
  await page.setViewportSize({width: 1440, height: 1100});
  const scanImport = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v2/documents");
  await page.getByLabel("Open PDF", {exact: true}).setInputFiles({name: `scan-${Date.now()}.pdf`, mimeType: "application/pdf", buffer: await fs.readFile(new URL("./fixtures/reader-scan.pdf", import.meta.url))});
  const scan = await (await scanImport).json();
  await page.getByRole("button", {name: "Use recognized text from section 1", exact: true}).waitFor({timeout: 120000});
  await idle();
  await page.getByRole("button", {name: "Use recognized text from section 1", exact: true}).click();
  await page.getByLabel("Extracted text 1", {exact: true}).waitFor();
  assert.match(await page.getByLabel("Extracted text 1", {exact: true}).inputValue(), /book/);
  const scannedPage = await get(`/documents/${scan.id}/pages/1`);
  assert.equal(scannedPage.regions[0].source_approved, false);
  assert.ok(scannedPage.regions[0].alternatives.length);
  checks.push("scanned PDF OCR suggestion adoption preserves evidence and requires review");
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  console.log(JSON.stringify({passed: true, checks, screenshots: out}));
} catch (e) {
  await page.screenshot({path: out + "failure.png", fullPage: true});
  console.error((await page.locator("body").innerText()).slice(0,4500)); throw e;
} finally {await browser.close();}
