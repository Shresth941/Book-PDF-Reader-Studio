/** Uses the real Chrome translation component. First use needs internet to download the language pack. */
import {chromium} from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import {fileURLToPath} from "node:url";

const base = new URL(process.env.REVIEW_URL || "http://127.0.0.1:8002");
assert.ok(["localhost", "127.0.0.1"].includes(base.hostname) && base.port && base.port !== "8000");
const profile = fileURLToPath(new URL("../../tmp/google-chrome-test", import.meta.url));
const browser = await chromium.launchPersistentContext(profile, {channel: "chrome", headless: true,
  ignoreDefaultArgs: ["--disable-component-update", "--disable-background-networking"]});
const fixture = await fs.readFile(new URL("./fixtures/reader.pdf", import.meta.url));
const samples = [
  {source: "en", target: "hi", text: "The court will not hear case 42 on Monday.",
    patterns: [/सोमवार/, /42|४२/, /नहीं/, /कोर्ट|अदालत|न्यायालय/, /सुन/]},
  {source: "en", target: "hi", text: "The applicant must file the appeal within 30 days.",
    patterns: [/आवेदक/, /अपील/, /30|३०/, /भीतर|अंदर/, /होग|चाहिए|करना/]},
  {source: "hi", target: "en", text: "आवेदक को 30 दिनों के भीतर अपील दायर करनी होगी।",
    patterns: [/applicant/i, /appeal/i, /within 30 days/i, /must|shall|will have to|has to/i]},
  {source: "hi", target: "en", text: "न्यायालय सोमवार को मामले की सुनवाई करेगा।",
    patterns: [/court/i, /Monday/i, /hear/i, /case|matter/i]},
];
const page = await browser.newPage();
const errors = [];
page.on("pageerror", e => errors.push(e.message));
const idle = () => page.waitForFunction(() => !document.querySelector('input[aria-label="Open PDF"]')?.disabled);
try {
  for (const sample of samples) {
    await page.goto(base.href);
    await idle();
    const uploaded = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v2/documents");
    await page.getByLabel("Open PDF", {exact: true}).setInputFiles({name: `google-${sample.source}-${Date.now()}.pdf`, mimeType: "application/pdf", buffer: fixture});
    const doc = await (await uploaded).json();
    const source = page.getByLabel("Extracted text 1", {exact: true});
    await source.waitFor({timeout: 120000}); await idle(); await source.fill(sample.text);
    await page.getByRole("button", {name:"Save changes", exact:true}).click(); await idle();
    await page.getByLabel("Source language", {exact:true}).selectOption(sample.source);
    await page.getByLabel("Translation language", {exact:true}).selectOption(sample.target);
    await page.getByLabel("Translator", {exact:true}).selectOption("chrome");
    const translate = page.getByRole("button", {name:"Translate page", exact:true});
    assert.ok(await translate.isDisabled());
    await page.getByRole("checkbox", {name:/I checked the extracted text/}).check();
    const applied = page.waitForResponse(r => r.url().endsWith("/chrome/apply"), {timeout: 240000});
    await translate.click();
    const response = await applied;
    assert.equal(response.status(),200,await response.text());
    await idle();
    const result = await page.getByLabel("Translation 1", {exact:true}).inputValue();
    console.log(sample.text, "=>", result);
    for (const pattern of sample.patterns) assert.match(result, pattern);
    const saved = await (await page.request.get(new URL(`/api/v2/documents/${doc.id}/pages/1`, base).href)).json();
    assert.equal(saved.regions[0].translation,result); assert.equal(saved.regions[0].model,"chrome-on-device");
    assert.equal(saved.regions[0].translation_approved,false);
  }
  // Cancel after preparing a revision-checked page and before committing any result.
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  let applyCalls = 0;
  page.on("request", req => {if (req.url().endsWith("/chrome/apply")) applyCalls++;});
  await page.route("**/chrome/plan", async route => {await hold; await route.continue();}, {times: 1});
  const planning = page.waitForRequest(req => req.url().endsWith("/chrome/plan"));
  await page.getByRole("checkbox", {name:/I checked the extracted text/}).check();
  await page.getByRole("button", {name:"Translate page again", exact:true}).click();
  await planning;
  await page.getByRole("button", {name:"Cancel Google translation", exact:true}).click();
  release();
  await page.getByText("Translation cancelled.", {exact:true}).waitFor();
  assert.equal(applyCalls, 0);
  // Check unsupported browsers and consent controls without contacting a paid provider.
  await browser.addInitScript(() => {
    Object.defineProperty(window, "Translator", {value: undefined, configurable: true});
    Object.defineProperty(navigator, "userAgent", {value: "Unsupported test browser", configurable: true});
  });
  await page.route("**/api/v2/capabilities", async route => {
    const original = await route.fetch();
    await route.fulfill({json: {...await original.json(), google_comparison: true}});
  });
  let calls = 0;
  await page.route("**/google-compare", async route => {
    const payload = route.request().postDataJSON();
    assert.equal(payload.approve_send, true); assert.equal(payload.public_nonpersonal, true);
    assert.equal(payload.text, "This is a book."); calls++;
    await route.fulfill({json: {translation: "यह एक पुस्तक है।"}});
  });
  await page.reload(); await idle();
  await page.getByLabel("Open PDF", {exact: true}).setInputFiles({name: `google-unsupported-${Date.now()}.pdf`, mimeType: "application/pdf", buffer: fixture});
  await page.getByLabel("Extracted text 1", {exact:true}).waitFor(); await idle();
  await page.waitForFunction(() => document.querySelector('select[aria-label="Translator"] option[value="chrome"]')?.disabled);

  assert.equal(await page.getByLabel("Translator", {exact:true}).inputValue(), "local");
  await page.getByText("Compare a passage in Google Translate", {exact:true}).click();
  const send = page.getByRole("button", {name:"Translate passage with Google Cloud", exact:true});
  assert.ok(await send.isDisabled()); assert.equal(calls,0);
  await page.getByRole("checkbox", {name:/I approve sending this public/}).check(); await send.click();
  await page.getByText("यह एक पुस्तक है।", {exact:false}).last().waitFor();
  assert.equal(calls,1); assert.ok(await send.isDisabled());
  assert.deepEqual(errors,[]);
} finally {await browser.close();}
