import React from "react";

export function PublicLauncher() {
  return <div className="launcher">
    <header className="launch-header"><a href="#top" className="launch-brand"><span aria-hidden="true">P</span> PDF Reader Studio</a><span className="launch-badge">Local processing</span></header>
    <main id="top">
      <section className="launch-hero">
        <div><p className="launch-eyebrow">YOUR DOCUMENTS. YOUR COMPUTER.</p><h1>A careful path from PDF<br/>to reviewed translation.</h1>
          <p className="launch-lead">Read the original, check what was recognised, review Hindi–English translation drafts, and inspect the finished pages.</p>
          <a className="launch-primary" href="http://127.0.0.1:8000/" target="_blank" rel="noreferrer">Open local workspace <span aria-hidden="true">↗</span></a>
          <p className="launch-hint">Start the app on this Windows computer first. It opens in a new tab.</p>
        </div>
        <aside className="launch-flow" aria-label="Review workflow"><p className="launch-eyebrow">FROM ORIGINAL TO REVIEWED COPY</p>
          <ol><li><span>01</span><div><strong>Keep the original</strong><p>Work beside the saved source PDF.</p></div></li>
            <li><span>02</span><div><strong>Check the content</strong><p>Review text, table cells and form values.</p></div></li>
            <li><span>03</span><div><strong>Review the meaning</strong><p>Check AI drafts, names, numbers and terms.</p></div></li>
            <li><span>04</span><div><strong>Inspect every export page</strong><p>Resolve issues before final approval.</p></div></li></ol>
        </aside>
      </section>
      <section className="launch-notice"><strong>This website is the launch and setup page.</strong><p>The document workspace, PDF files and AI models run on your computer. This website has no document upload or cloud processing. The local server must be running when you open the workspace.</p></section>
      <section className="launch-setup" aria-labelledby="setup-title"><div><p className="launch-eyebrow">START HERE</p><h2 id="setup-title">Open the app on your computer</h2><p>Use the PDF Reader project folder provided by the project owner. A standalone installation download is not yet available.</p></div>
        <div className="launch-steps"><article><h3>1. Start the local server</h3><p>Open PowerShell in your PDF Reader project folder and run:</p><pre><code>.\Start-Local.ps1</code></pre><p>Keep that window running, then use <strong>Open local workspace</strong> above.</p></article>
          <details><summary>First setup on a new Windows computer</summary><p>Install Python 3.12, Node.js and Tesseract OCR first. In the project folder, run:</p><pre><code>.\Setup-Local.ps1 -InstallOcrData -InstallTranslationModels</code></pre><p>Setup downloads dependencies and the two public translation models. Afterwards, the core workflow runs offline. Windows Hindi fonts are used for PDF output. Setup is still a development workflow, not a signed installer.</p></details>
          <details><summary>The workspace does not open</summary><p>Check that the server window is still running on this same computer. Open <a href="http://127.0.0.1:8000/" target="_blank" rel="noreferrer">http://127.0.0.1:8000</a>. A server running on another computer does not count as local to this browser.</p></details>
          <details><summary>Processing paused for available RAM</summary><p>The app requires at least 1 GB of available system memory before starting a processing worker. Close unused apps or browser tabs, use <strong>Check available RAM</strong>, then retry inspection or resume the job. Your original and saved work are retained; do not upload another copy.</p></details>
        </div>
      </section>
      <section className="launch-review"><h2>AI drafts need your judgment.</h2><p>This is a development build. Translation models can change names, amounts, roles and legal meaning. Review every segment and the exported layout. Complex documents and long books still need broader qualification; no universal accuracy or legal correctness is promised.</p></section>
    </main>
    <footer><span>PDF Reader Studio</span><span>Local review workspace · Development build</span></footer>
  </div>;
}
