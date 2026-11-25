/** Offline legal headings and translation expansion, using an isolated QA server. */
import {chromium} from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
const base = process.env.REVIEW_URL || "http://127.0.0.1:8002";
assert.ok(["localhost", "127.0.0.1"].includes(new URL(base).hostname) && new URL(base).port !== "8000");
const browser = await chromium.launch({channel: "chrome", headless: true});
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
page.setDefaultTimeout(30000);
const errors = [], external = [];
page.on("pageerror", e => errors.push(e.message));
await page.route("**/*", route => {
  const u = new URL(route.request().url());
  if (/^https?:$/.test(u.protocol) && u.origin !== new URL(base).origin) {external.push(u.href); return route.abort();}
  return route.continue();
});
const idle = () => page.waitForFunction(() => !document.querySelector('input[aria-label="Open PDF"]')?.disabled, undefined, {timeout: 120000});
try {
  await page.goto(base); await idle();
  const uploaded = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v2/documents");
  await page.getByLabel("Open PDF", {exact: true}).setInputFiles({name: `law-${Date.now()}.pdf`, mimeType: "application/pdf", buffer: await fs.readFile(new URL("./fixtures/reader.pdf", import.meta.url))});
  const doc = await (await uploaded).json();
  await page.getByLabel("Extracted text 1", {exact: true}).waitFor({timeout: 120000}); await idle();
  assert.equal(await page.getByLabel("Translation style").inputValue(), "legal");
  await page.getByLabel("Extracted text 1", {exact: true}).fill("भारत का संविधान\n(भाग 3—मूल अधिकार)");
  await page.getByRole("button", {name: "Save changes", exact: true}).click(); await idle();
  await page.getByLabel("Source language", {exact: true}).selectOption("hi");
  await page.getByLabel("Translation language", {exact: true}).selectOption("en");
  await page.getByRole("checkbox", {name: /I checked the extracted text/}).check();
  await page.getByRole("button", {name: "Translate page", exact: true}).click();
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="Translation 1"]')?.value.includes("Fundamental Rights")); await idle();
  assert.equal(await page.getByLabel("Translation 1", {exact: true}).inputValue(), "Constitution of India\n(Part 3 — Fundamental Rights)");
  const response = await page.request.get(`${base}/api/v2/documents/${doc.id}/pages/1`);
  assert.equal((await response.json()).regions[0].translation_approved, false);
  // Add a second passage beneath the first in this disposable QA document.
  const token = await (await page.request.get(`${base}/api/v2/session`)).json();
  const current = await (await page.request.get(`${base}/api/v2/documents/${doc.id}/pages/1`)).json();
  const added = await page.request.post(`${base}/api/v2/documents/${doc.id}/pages/1/regions`, {headers: {"X-Review-Token": token.token}, data: {revision: current.revision, bbox: [72, 180, 450, 205]}});
  assert.ok(added.ok(), await added.text());
  const next = await added.json();
  const second = next.regions.find(r => !current.regions.some(old => old.id === r.id));
  const edited = await page.request.patch(`${base}/api/v2/documents/${doc.id}/pages/1/regions/${second.id}`, {headers: {"X-Review-Token": token.token}, data: {revision: next.revision, source: "उच्चतम न्यायालय", translation: "Supreme Court", source_language: "hi", disposition: "translate", contains_text: true, kind: "heading"}});
  assert.ok(edited.ok(), await edited.text());
  await page.reload(); await idle();
  await page.getByRole("button", {name: new RegExp(doc.filename)}).click(); await idle();
  await page.getByRole("button", {name: "Translation", exact: true}).click();
  const long = "The provisions of this Constitution shall apply subject to the exceptions specified herein. ".repeat(100);
  await page.getByLabel("Translation 1", {exact: true}).fill(long);
  await page.getByRole("button", {name: "Save changes", exact: true}).click(); await idle();
  for (const zoom of ["100", "150"]) {
    await page.getByLabel("Page zoom", {exact: true}).selectOption(zoom);
    const geometry = await page.getByLabel("Translation 1", {exact: true}).evaluate(el => ({clipped: el.scrollHeight > el.clientHeight + 2, height: el.parentElement.offsetHeight, sheet: el.closest('.page-text-sheet').offsetHeight, bottom: el.parentElement.offsetTop + el.parentElement.offsetHeight}));
    assert.equal(geometry.clipped, false);
    assert.ok(geometry.height > 800 && geometry.sheet >= geometry.bottom);
    const followingTop = await page.getByLabel("Translation 2", {exact: true}).evaluate(el => el.parentElement.offsetTop);
    assert.ok(followingTop >= geometry.bottom + 3, "Following passage must move below the expanded translation");
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.getByLabel("Translation 1", {exact: true}).inputValue(), long);
  await page.screenshot({path: "../tmp/legal-mobile.png", fullPage: true});
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  console.log("PASS legal default, offline heading, no automatic approval, long translation grows sheet, zoom, mobile, no external browser requests");
} finally {await browser.close();}
