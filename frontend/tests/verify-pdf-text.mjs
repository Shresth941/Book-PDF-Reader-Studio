// Independent local qualification; Node 24 and pinned PDF.js 6.3.289.
// Usage: node verify-pdf-text.mjs document.pdf expectations.json /path/to/pdf.mjs
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {pathToFileURL} from "node:url";
import {createRequire} from "node:module";

const [pdfPath, expectedPath, modulePath] = process.argv.slice(2);
assert.ok(pdfPath && expectedPath && modulePath, "Provide PDF, expectations JSON and PDF.js module paths.");
const moduleUrl = pathToFileURL(path.resolve(modulePath));
const pdfjs = await import(moduleUrl.href);
assert.equal(pdfjs.version, "6.3.289", "Requalify before changing the PDF.js version.");
const expected = JSON.parse(await fs.readFile(expectedPath, "utf8"));
const loading = pdfjs.getDocument({data: new Uint8Array(await fs.readFile(pdfPath)), isEvalSupported: false, verbosity: 0});
const document = await loading.promise;
try {
  const pages = [];
  for (let number = 1; number <= document.numPages; number++) {
    const page = await document.getPage(number);
    const content = await page.getTextContent();
    pages.push(content.items.filter(i => "str" in i).map(i => i.str + (i.hasEOL ? "\n" : "")).join(""));
    if (number === 1 && expected.render_first_page) {
      const {createCanvas} = createRequire(moduleUrl)("@napi-rs/canvas");
      const viewport = page.getViewport({scale: 1.5});
      const canvas = createCanvas(Math.ceil(viewport.width), Math.ceil(viewport.height));
      await page.render({canvasContext: canvas.getContext("2d"), viewport}).promise;
      await fs.writeFile(pdfPath + ".pdfjs.png", canvas.toBuffer("image/png"));
    }
  }
  // Collapse line breaks without removing spaces inside incorrectly split words.
  const actual = pages.join("\n").normalize("NFC").replace(/\s+/g, " ").trim();
  for (const token of expected.forbidden ?? ["\u0000", "\uFFFD"]) {
    assert.ok(!actual.includes(token), `Unexpected character ${JSON.stringify(token)}`);
  }
  const counts = [];
  for (const entry of expected.occurrences ?? []) {
    const value = entry.text.normalize("NFC").replace(/\s+/g, " ").trim();
    assert.ok(value.length);
    const count = actual.split(value).length - 1;
    assert.equal(count, entry.count, `Incorrect occurrence count for ${entry.text}`);
    counts.push({text: entry.text, count});
  }
  if (expected.full_text !== undefined) {
    assert.equal(actual, expected.full_text.normalize("NFC").replace(/\s+/g, " ").trim());
  }
  const result = {passed: true, reader: "PDF.js", version: pdfjs.version, pages: document.numPages, counts,
    rendered_first_page: Boolean(expected.render_first_page)};
  await fs.writeFile(pdfPath + ".pdfjs-verification.json", JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {
  await loading.destroy();
}
