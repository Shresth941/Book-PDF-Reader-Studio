# Full Vercel deployment packaging

These templates package the hosted adapter, not the old static launcher and not
the local workspace's documents. Local operation is unchanged.

1. Build the frontend in its hosted mode using Node 24. Pass the resulting output
   directory to `tools/prepare-vercel-full.ps1 -FrontendBuildDirectory <path>`.
2. Set the hosted adapter's required secrets in Vercel project settings. Do not
   put them in this folder. Keep the Blob store private and protect each workspace.
3. Enable Fluid compute and `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` for this existing
   project. The combined OCR, translation libraries and models may exceed the
   normal Python bundle size. This does not increase Hobby's 2 GB memory limit.
4. Deploy only the fresh stage returned by the script. The build hook compiles
   official pinned Leptonica/Tesseract sources, bundles linked native libraries
   and their licenses, and obtains a pinned SIL-OFL Noto Devanagari font. A missing
   dependency is a failed build, not a silent OCR-disabled deployment.
5. The entrypoint is `app.hosting.app:app`. The separately discovered subscriber
   is `app.hosting.queue_worker:process_message`. Root `public/` contains only
   the built interface. Each function has a 300-second ceiling.

The hosted bootstrap must prepend `vendor/native/bin` to `PATH` before importing
the recognition modules, set the absolute `models` directory, and leave worker
execution at the stage root so the existing `models/fonts` resolver works.
The Tesseract wrapper supplies its bundled library path only to Tesseract.

Staging copies Python source, the built interface, the exact two existing
converted Marian model directories, and the three OCR language files. Model
hashes are checked during assembly. Source archives, Windows binaries/fonts,
virtual environments, SQLite, PDFs, user backups and `.env` files are excluded.
The old OCR manifest's absolute personal paths are replaced by relative names.
No existing staging folder is removed or reused. The audit manifest remains
beside, rather than inside, the deployment stage.

Direct Python versions are pinned in `pyproject.toml`; the script also emits
`requirements.txt`. This is not yet a complete transitive lock. Linux wheel
provenance confirms availability of the exact model runtime versions; it does
not claim that Linux OCR, inference or exports have been qualified. Build tools
and codec packages come from the Vercel Amazon Linux 2023 package repositories;
their installed versions/licenses are recorded by the build hook.

The native build and deployment have not been executed by the packaging agent.
No additional tests were run, following the user's instruction.

Official references checked 2026-09-26:

- [FastAPI entrypoint, build hook and public directory](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel build image and dnf packages](https://vercel.com/docs/builds/build-image)
- [Python Queue subscriber configuration](https://pypi.org/project/vercel-queue/0.9.0/)
- [Large Functions opt-in](https://vercel.com/changelog/vercel-functions-can-now-be-up-to-5-gb-in-package-size)
- [Tesseract source](https://github.com/tesseract-ocr/tesseract/tree/db0ec62f81b0737fbbe184d8fea40af5738f8eef)
- [Leptonica source](https://github.com/DanBloomberg/leptonica/tree/63aef18d98432b8582a1565e241f7bd2ee9cc8d9)
- [Noto source and SIL OFL license](https://github.com/notofonts/noto-fonts/tree/ffebf8c1ee449e544955a7e813c54f9b73848eac)
