/** Real native-table and stale AcroForm review through the browser; no model or cloud use. */
import {chromium} from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";

// Never add synthetic documents to the user's normal workspace on port 8000.
assert.ok(process.env.REVIEW_URL, "Set REVIEW_URL to a separately configured QA workspace.");
const base = new URL(process.env.REVIEW_URL);
assert.ok(base.protocol === "http:" && ["127.0.0.1", "localhost"].includes(base.hostname)
  && base.port && base.port !== "8000", "Structure QA requires a separate loopback port, never production 8000.");
const fixture = fileURLToPath(new URL("./fixtures/structure.pdf", import.meta.url));
const out = fileURLToPath(new URL("../../tmp/structure-browser-qa/", import.meta.url));
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
const checks = [];
async function clickMutation(name, suffix, method = "POST", expected = 200) {
  const responsePromise = page.waitForResponse(response =>
    response.request().method() === method && new URL(response.url()).pathname.endsWith(suffix));
  await page.getByRole("button", {name, exact: true}).click();
  const response = await responsePromise;
  const body = await response.json();
  assert.equal(response.status(), expected, JSON.stringify(body));
  await page.waitForFunction(() => !document.querySelector('input[type="file"]')?.disabled);
  return body;
}
try {
  await page.goto(base.href);
  await page.getByRole("button", {name: "Detailed review", exact: true}).click();
  await page.getByRole("heading", {name: "PDF Reader Studio", exact: true}).waitFor();
  await page.waitForFunction(() => !document.querySelector('input[type="file"]')?.disabled);
  const importResponse = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v2/documents");
  await page.locator('input[type="file"]').setInputFiles(fixture);
  const imported = await importResponse;
  assert.ok(imported.ok(), await imported.text());
  const document = await imported.json();
  await page.getByText("Original saved. Review inspection status, then extract the pages.", {exact: true}).waitFor();
  await page.getByRole("button", {name: "Extract page", exact: true}).click();
  await page.getByText(/extract · completed/).waitFor({timeout: 120000});
  const readPage = async () => {
    const response = await page.request.get(new URL(`/api/v2/documents/${document.id}/pages/1`, base).href);
    assert.ok(response.ok(), await response.text());
    return response.json();
  };
  let data = await readPage();
  assert.equal(data.table_proposals.tables.length, 1);
  const tableId = data.table_proposals.tables[0].id;
  const tableRoute = `/tables/${tableId}/review`;
  const tableSummary = page.getByRole("button", {name: /Table 1 · 2 rows × 2 columns/});
  await tableSummary.click();
  const grid = page.locator(".structure-table");
  assert.equal(await grid.locator("td").count(), 4);
  assert.match(await grid.innerText(), /Row 1, column 1[\s\S]*Name[\s\S]*Row 1, column 2[\s\S]*Amount[\s\S]*Row 2, column 1[\s\S]*Alice[\s\S]*Row 2, column 2/);
  const adopt = page.getByRole("button", {name: "Create reviewable table cells", exact: true});
  assert.equal(await adopt.isDisabled(), true);
  await page.getByLabel("Table 1 review note", {exact: true}).fill("Checked");
  assert.equal(await adopt.isDisabled(), true);
  await page.getByLabel("Table 1 review note", {exact: true}).fill("Checked the Name and Amount headers, Alice row, blank amount and all four cell boundaries.");
  data = await clickMutation("Create reviewable table cells", tableRoute);
  assert.equal(data.regions.filter(region => region.table_id === tableId).length, 4);
  assert.equal(data.table_proposals.tables[0].structure_approved, false);
  checks.push("native table preview preserves row/column relationships", "adoption requires a specific note and does not approve structure");

  await grid.getByRole("button", {name: "Alice", exact: true}).click();
  await page.getByText("Table cell: row 2, column 1; spans 1 rows and 1 columns.", {exact: false}).waitFor();
  assert.equal(await page.getByLabel("Verified source", {exact: true}).inputValue(), "Alice");
  const aliceId = await page.getByLabel("Region", {exact: true}).inputValue();
  await grid.getByRole("button", {name: "Empty cell", exact: true}).click();
  assert.equal(await page.getByLabel("Verified source", {exact: true}).inputValue(), "");
  assert.equal(await page.getByLabel("Region disposition", {exact: true}).inputValue(), "decorative");
  await page.getByText("Table cell: row 2, column 2; spans 1 rows and 1 columns.", {exact: false}).waitFor();
  data = await clickMutation("I checked headers, values, blank cells and spans", tableRoute);
  assert.equal(data.table_proposals.tables[0].structure_approved, true);
  await page.getByRole("button", {name: /Table 1.*structure reviewed/}).waitFor();
  await page.screenshot({path: path.join(out, "table-reviewed.png"), fullPage: true});
  await grid.getByRole("button", {name: "Alice", exact: true}).click();
  await page.getByLabel("Verified source", {exact: true}).fill("Alice corrected");
  assert.equal(await page.getByRole("button", {name: "I checked headers, values, blank cells and spans", exact: true}).isDisabled(), true);
  data = await clickMutation("Save changes", `/regions/${aliceId}`, "PATCH");
  assert.equal(data.table_proposals.tables[0].structure_approved, false);
  await page.getByRole("button", {name: /Table 1.*needs review/}).waitFor();
  assert.equal(await grid.getByRole("button", {name: "Alice corrected", exact: true}).count(), 1);
  checks.push("cell and blank-cell selection target the correct regions", "structure approval is invalidated by a cell edit");

  const form = data.regions.find(region => region.method === "form-field");
  assert.ok(form, "A form field must remain separate from table cells.");
  assert.equal(form.form_evidence.status, "contradiction");
  await page.getByLabel("Region", {exact: true}).selectOption(form.id);
  await page.getByRole("heading", {name: "Reconcile this form field", exact: true}).waitFor();
  const formPanel = page.locator(".review-warning").filter({has: page.getByRole("heading", {name: "Reconcile this form field", exact: true})});
  assert.match(await formPanel.innerText(), /Stored value\s+Stored newer value/);
  assert.match(await formPanel.innerText(), /Text from the existing appearance\s+Visible original/);
  assert.equal(await page.getByLabel("Region disposition", {exact: true}).inputValue(), "unreadable");
  await page.getByLabel("Verified source", {exact: true}).fill("Visible original");
  await page.getByLabel("Confirm source language", {exact: true}).selectOption("en");
  await page.getByLabel("Region disposition", {exact: true}).selectOption("translate");
  await clickMutation("Save changes", `/regions/${form.id}`, "PATCH");
  const denied = await clickMutation("Approve source recognition", "/approvals", "POST", 422);
  assert.equal(denied.code, "form_reconciliation_required");
  await page.getByText("Compare the stored field value and visible appearance, then save a reconciliation note before approving the source.", {exact: true}).waitFor();
  assert.equal((await readPage()).regions.find(region => region.id === form.id).source_approved, false);
  const note = "Compared the preserved original appearance: Visible original is the verified text; the newer stored value is stale.";
  await page.getByLabel("Form reconciliation note", {exact: true}).fill(note);
  await clickMutation("Save changes", `/regions/${form.id}`, "PATCH");
  data = await clickMutation("Approve source recognition", "/approvals");
  const approved = data.regions.find(region => region.id === form.id);
  assert.equal(approved.source_approved, true);
  assert.deepEqual(approved.form_evidence, form.form_evidence);
  await page.getByRole("button", {name: "✓ Source reviewed", exact: true}).waitFor();
  await page.screenshot({path: path.join(out, "form-reconciled.png"), fullPage: true});
  checks.push("stored and existing appearance values are displayed separately", "form approval rejects missing reconciliation notes", "review retains original form evidence");

  await page.getByText("Refresh form evidence", {exact: true}).click();
  const preview = await clickMutation("Inspect form evidence", "/form-evidence/preview");
  assert.equal(preview.inspection.form_evidence.inventory_complete, true);
  assert.equal(preview.inspection.regions.length, 1);
  assert.equal((await readPage()).revision, data.revision, "Inspection must not edit the page.");
  await page.getByLabel("Saved region for field 1", {exact: true}).selectOption("__new__");
  await page.getByLabel("Evidence mapping review note", {exact: true}).fill("Matched the original applicant widget to its existing reviewed source region.");
  assert.equal(await page.getByRole("button", {name: "Apply evidence mappings", exact: true}).isDisabled(), true);
  await page.getByLabel("Saved region for field 1", {exact: true}).selectOption(form.id);
  const refreshed = await clickMutation("Apply evidence mappings", "/form-evidence/apply");
  assert.equal(refreshed.revision, data.revision, "Identical evidence must not invalidate existing review.");
  const retained = refreshed.regions.find(region => region.id === form.id);
  assert.equal(retained.source_approved, true);
  assert.equal(retained.source, "Visible original");
  assert.equal(retained.form_review_note, note);
  checks.push("bounded form inspection leaves edits unchanged", "unmapped saved fields block apply", "identical evidence refresh retains approved source and note");

  await page.reload();
  await page.locator(".library-item").filter({hasText: "structure.pdf"}).first().click();
  await page.getByLabel("Region", {exact: true}).selectOption(form.id);
  assert.equal(await page.getByLabel("Form reconciliation note", {exact: true}).inputValue(), note);
  await page.getByRole("button", {name: "✓ Source reviewed", exact: true}).waitFor();
  await page.getByRole("button", {name: /Table 1 · 2 rows × 2 columns/}).click();
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(out, "structure-mobile.png"), fullPage: true});
  const width = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, viewport: window.innerWidth}));
  assert.ok(width.scroll <= width.viewport + 2, `Horizontal overflow: ${JSON.stringify(width)}`);
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  checks.push("form review persists after reload", "mobile table and form panels fit viewport", "no browser exceptions", "no external network requests");
  console.log(JSON.stringify({passed: true, checks, screenshots: out, document_id: document.id}));
} catch (error) {
  await page.screenshot({path: path.join(out, "failure.png"), fullPage: true}).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
