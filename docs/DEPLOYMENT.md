# GitHub and Vercel deployment

## Current architecture

The React/Vite frontend can be deployed on Vercel with Root Directory set to
`frontend`. The FastAPI backend currently saves uploaded PDFs on a local disk
and reads them in later requests. It needs a backend host with persistent disk,
or a storage adapter for durable cloud object storage before running on Vercel.
Temporary function storage cannot reliably preserve PDFs across requests or
instances. OCR also uses native dependencies and model files, whose bundle size
must be checked before deploying the backend as a function.

The included Vercel configuration deploys the frontend only. Deploying it without
a reachable backend will display the UI but will not provide a working PDF app.

## Access needed to publish

- GitHub: repository URL (or account and desired repository name) and a signed-in
  GitHub CLI / Git Credential Manager session with permission to push. Creating
  a repository also requires repository creation permission. Prefer a private
  repository unless public visibility is explicitly desired.
- Vercel: a signed-in Vercel CLI or browser session and access to the destination
  account/team. An existing project name is useful if reusing a project.
- Backend: the selected hosting account and HTTPS backend URL. An all-Vercel
  deployment additionally needs durable storage and the appropriate server-side
  storage credentials after the storage integration is implemented.
- Optional: `LIBRETRANSLATE_URL` and `LIBRETRANSLATE_API_KEY` for a configured
  translation provider. Basic PDF upload/extraction needs no translation key.

Use service login flows; do not paste passwords or access tokens into chat.
GitHub/Vercel access tokens are deployment credentials, not application runtime
settings, and do not belong in the frontend or repository.

## Environment variables

| Location | Variable | Value |
| --- | --- | --- |
| Vercel frontend | `VITE_API_BASE_URL` | Backend HTTPS URL without a trailing slash |
| Backend host | `ALLOWED_ORIGINS` | Exact frontend origins, comma-separated |
| Backend host | `UPLOAD_DIR` | Persistent disk mount directory for uploaded PDFs |
| Backend host | `MAX_UPLOAD_BYTES` | Optional positive upload limit in bytes |
| Backend host | `LIBRETRANSLATE_URL` | Optional translation service URL |
| Backend host | `LIBRETRANSLATE_API_KEY` | Optional secret translation key |

For local development, copy the example files to `backend/.env` and
`frontend/.env` if they do not already exist. Filled `.env` files, uploaded PDFs,
logs, local environments, and build output are excluded from Git. Keep security
instructions in documentation; put secret configuration values in `.env` locally
and in the host's environment settings in production. `.env` files are plaintext,
not encrypted storage. Never put secrets in variables beginning with `VITE_`:
those values are included in the browser build.

## Deployment steps

1. Push the reviewed source files and placeholder `.env.example` files to GitHub.
2. Deploy the backend to the selected host with its persistent storage attached.
   From the `backend` directory, install `requirements.txt` and start
   `uvicorn app.main:app --host 0.0.0.0 --port <host-provided-port>`.
3. Import the GitHub repository into Vercel. Set Root Directory to `frontend`;
   the included configuration uses Vite, `npm ci`, `npm run build`, and `dist`.
4. Set `VITE_API_BASE_URL` to the backend's HTTPS URL in the Vercel environments
   you will deploy. Redeploy after changing frontend environment variables.
5. Set backend `ALLOWED_ORIGINS` to the exact deployed frontend origin. Add
   preview origins individually if needed; avoid allowing every origin.
6. Verify backend `/health`, upload a synthetic PDF in the deployed frontend,
   read its pages, and test exports. Check translation separately if configured.

## Privacy boundary

The current API has no user authentication or document ownership checks. CORS
does not provide authentication. Before accepting confidential documents on an
internet-facing deployment, implement access control or place the entire app
behind appropriate access protection. Translation and dictionary features can
send selected text to external providers, including fallback services.

## Official references

- [Vite on Vercel](https://vercel.com/docs/frameworks/frontend/vite)
- [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel environment variables](https://vercel.com/docs/environment-variables)

## Recommended backend: Render

Use a Python web service with Root Directory `backend`, Build Command
`pip install -r requirements.txt`, and Start Command
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
Set Health Check Path to `/health`. Attach a persistent disk at `/var/data`
and set `UPLOAD_DIR=/var/data/uploads`. Set `ALLOWED_ORIGINS` to the exact
Vercel frontend origin. Persistent disks require a paid service; confirm the
service and disk cost before creating paid resources. No paid resources have
been provisioned by this setup.

See [Render persistent disks](https://render.com/docs/disks) and
[Render web services](https://render.com/docs/web-services).
