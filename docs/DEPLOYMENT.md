# Vercel launcher deployment

## Full application deployment status — 2026-09-25

The user has explicitly waived further testing and requested the full application. Testing is no longer the deployment gate. The current local backend still needs a hosting migration; the existing static launcher is not a full application deployment.

Repository findings: hosted mode disables the current `/api/v2` API, while local mode rejects public hostnames. The current frontend assumes a same-origin API. SQLite/WAL, original documents, exports and backups require persistent writable storage. The job consumer is a process-resident thread and uses subprocesses for parsing/OCR/models. Session tokens, PDF passwords and worker serialization are process-local. Public account authentication/document ownership are not implemented, so simply removing the localhost check would expose the workspace. Translation wheel hashes are currently Windows-specific.

The smallest migration preserving the current workflow is one persistent backend instance with a durable private volume, installed OCR/models/fonts and authentication on every route, with the current React review interface on Vercel. Configure an authenticated HTTPS backend connection explicitly; do not switch to the obsolete hosted frontend or merely loosen the local boundary. Provision cloud storage independently; do not upload the local user workspace as deployment source.

Vercel's official documentation, checked on 2026-09-25, lists Hobby Functions at 2 GB memory and 300 seconds, a 4.5 MB function request/response limit, and read-only filesystems with 500 MB temporary scratch space. Larger function bundles/container support does not supply persistent SQLite storage or preserve the current background-thread lifecycle. An all-Vercel implementation would instead require durable external storage and a durable serverless job architecture plus authentication.

The Vercel CLI account is connected and the existing project is accessible. No persistent backend target or its deployment access has been established. No new deployment, purchase, source upload or test run occurred for this request. Obtain the backend provider/server target and access before applying provider-specific configuration. Retain the user's free-only constraint unless they explicitly change it.

Sources: [Function limits](https://vercel.com/docs/functions/limitations), [Runtime/filesystem support](https://vercel.com/docs/functions/runtimes).

## Existing public launcher

The current Vercel release is a static launch/setup page for the local Windows application. It opens http://127.0.0.1:8000 in a separate tab. The local server must run on the same computer as the browser. This page has no PDF upload, API proxy, model inference or access to the local database.

The previous three-service FastAPI/Blob deployment is superseded. Do not deploy the repository root: it contains local documents and models. The root `.vercelignore` denies all except the root configuration, which deliberately fails a direct build. A Git push is not part of the current deployment procedure.

## Build and prepare

From the project root:

```powershell
npm --prefix frontend run build:launcher
.\tools\prepare-vercel.ps1
```

The preparation command creates a fresh directory below `tmp/vercel-launcher-<unique-id>` and prints its absolute path and an adjacent file manifest. It copies only `index.html` and hashed JavaScript/CSS assets. Reparse points, unexpected files, more than 20 files and builds larger than 2 MB are rejected. The manifest records sizes and SHA-256 fingerprints. No original PDF, SQLite database, backup, model, environment file, screenshot or log is copied.

The staging directory uses Vercel Build Output API version 3 with static files only. This platform format version is unrelated to the application's PDF verification version 5. The content security policy disables script network requests and form submission. No runtime environment variables or credentials belong in this public bundle.

## Preview and production

Use the existing Vercel project `book-pdf-reader-studio` in `shresth-singhs-projects`. Authentication must already be available to the CLI. Never paste tokens into source files, logs or documentation.

```powershell
vercel deploy --prebuilt --cwd '<printed staging path>' --yes
# After verifying the same staged files:
vercel deploy --prebuilt --cwd '<printed staging path>' --prod --yes
```

The public production domain is https://book-pdf-reader-studio.vercel.app. Preview deployments can require the account's existing Vercel authentication; do not disable protection to test them. Use an authenticated CLI request when needed. Do not add `--public`, which can expose deployment source.

## Verification

1. Confirm that `/` and the two referenced static assets return successfully and match the local manifest fingerprints.
2. Check the content security policy, correct page title and visible local-server requirement.
3. Check desktop and mobile layout, help disclosures, console errors, and the local link destination.
4. Confirm `/api/health`, `/api/v2/documents` and private-looking paths return 404 rather than a live backend or file.
5. Verify the local application separately on port 8000. Remote static-page success is not qualification of OCR, translation, legal accuracy or long-book processing.
6. Record the deployment URL, immutable deployment ID, manifest and verification in `task.md`.

The development application still requires bilingual acceptance testing, broader document support and long-book resource qualification. The launcher must retain its development-build and review notices.

## Rollback

Keep prior deployment IDs in the release ledger. Restore a previous verified **static launcher** deployment through Vercel's rollback workflow if a new launcher fails. The older multi-service deployment is a different architecture and must not be restored automatically. Rebuilding and redeploying a previously verified static bundle is also possible; retain its manifest. Do not delete local documents or migrate the SQLite workspace as part of web deployment.

## Official references

- [Build Output API](https://vercel.com/docs/build-output-api)
- [Prebuilt CLI deployments](https://vercel.com/docs/cli/deploy)
- [Ignoring deployment files](https://vercel.com/docs/deployments/vercel-ignore)
