/** Requires isolated QA server and HINDI_ACCEPTANCE_PDF=path/to/coi-hindi.pdf. */
import {chromium} from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";

const base = new URL(process.env.REVIEW_URL || "http://127.0.0.1:8002");
assert.ok(["127.0.0.1", "localhost"].includes(base.hostname) && base.port && base.port !== "8000");
assert.ok(process.env.HINDI_ACCEPTANCE_PDF, "Set HINDI_ACCEPTANCE_PDF to the supplied local PDF");
const browser = await chromium.launch({channel: "chrome", headless: true});
const page = await browser.newPage({viewport: {width: 1400, height: 1000}});
page.setDefaultTimeout(30000);
const errors = [], timings = [];
page.on("pageerror", e => errors.push(e.message));
async function idle() {
  await page.waitForFunction(() => !document.querySelector('input[aria-label="Open PDF"]')?.disabled);
}
try {
  await page.goto(base.href);
  await idle();
  const imported = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v2/documents");
  await page.getByLabel("Open PDF", {exact: true}).setInputFiles(process.env.HINDI_ACCEPTANCE_PDF);
  assert.equal((await imported).status(), 201);
  await page.getByLabel("Extracted text 1", {exact: true}).waitFor({timeout: 120000});
  await idle();
  assert.equal(await page.getByLabel("Extracted text 1", {exact: true}).inputValue(), "भारत\nका\nसंविधान");
  assert.equal(await page.getByLabel("Source language", {exact: true}).inputValue(), "hi");
  assert.equal(await page.getByLabel("Translation language", {exact: true}).inputValue(), "en");
  for (const [number, phrases] of [[41, ["16.", "मूलवंश", "1956", "2001"]], [101, ["124.", "उच्चतम न्यायालय", "2015"]]]) {
    await page.getByLabel("Page", {exact: true}).selectOption(String(number));
    await page.getByRole("heading", {name: "Bring the text into view"}).waitFor();
    await idle();
    const start = Date.now();
    await page.getByRole("button", {name: "Read this page", exact: true}).click();
    await page.getByLabel("Extracted text 1", {exact: true}).waitFor({timeout: 120000});
    await idle();
    const text = (await page.locator("textarea").evaluateAll(es => es.map(e => e.value))).join("\n");
    for (const phrase of phrases) assert.ok(text.includes(phrase), `Page ${number}: ${phrase}`);
    assert.equal(await page.getByRole("group", {name: "Extracted text page", exact: true}).count(), 1);
    const layout = await page.locator(".page-text-sheet textarea").evaluateAll(es => es.map(e => ({
      border: getComputedStyle(e).borderWidth,
      clipped: e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1,
      x: e.parentElement.offsetLeft, y: e.parentElement.offsetTop,
    })));
    assert.ok(layout.every(item => item.border === "0px"));
    assert.ok(layout.every(item => !item.clipped), `Page ${number} should display all source text`);
    assert.ok(new Set(layout.map(item => Math.round(item.x))).size > 1, "Keep side notes and page margins");
    assert.ok(await page.locator(".page-line-preview").count() > 0, "Use original line positions");
    assert.ok(await page.locator(".page-line-text").evaluateAll(es => es.every(e => Number.parseFloat(getComputedStyle(e).letterSpacing) === 0 || getComputedStyle(e).letterSpacing === "normal")), "Do not separate Hindi combining letters");
    assert.equal(await page.getByLabel("Source language", {exact: true}).inputValue(), "hi");
    assert.ok(await page.getByRole("button", {name: "Translate page", exact: true}).isDisabled());
    timings.push({page: number, seconds: (Date.now() - start) / 1000});
  }
  const download = page.waitForEvent("download");
  await page.getByRole("button", {name: "Download this page’s extracted text", exact: true}).click();
  assert.match(await fs.readFile(await (await download).path(), "utf8"), /124\./);
  const first = page.getByLabel("Extracted text 1", {exact: true});
  const saved = await first.inputValue();
  await first.fill(saved + " edited");
  await first.blur();
  assert.equal(await first.locator("..").locator(".page-line-preview").count(), 0, "Edited text must not use stale geometry");
  await first.fill(saved);
  await page.getByRole("button", {name: "Save changes", exact: true}).click();
  await page.getByText("Changes saved. Your original PDF is unchanged.", {exact: true}).waitFor();
  const beforeZoom = await page.locator(".page-text-sheet").boundingBox();
  await page.getByLabel("Page zoom", {exact: true}).selectOption("150");
  const afterZoom = await page.locator(".page-text-sheet").boundingBox();
  assert.ok(Math.abs(afterZoom.width / beforeZoom.width - 1.5) < .02);
  await page.getByLabel("Page zoom", {exact: true}).selectOption("100");
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({passed: true, timings, checks: ["cover exact text", "Hindi to English defaults", "articles and dates", "review gate", "Unicode download", "mobile width", "no browser errors"]}, null, 2));
} finally { await browser.close(); }
