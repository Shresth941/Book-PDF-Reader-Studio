import React, {FormEvent, useEffect, useState} from "react";
import {ReviewWorkspace} from "./ReviewWorkspace";
import {resetReviewSession} from "./reviewApi";
import "./hosted.css";

async function authRequest(path: string, method = "GET", body?: {password: string}) {
  const response = await fetch(`/api/auth/${path}`, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: body ? {"Content-Type": "application/json"} : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof result.detail === "string" ? result.detail : `Unable to ${path === "login" ? "sign in" : path === "logout" ? "sign out" : "check your session"} (${response.status}). Please try again.`);
  }
  return result;
}

export function HostedWorkspace() {
  const [authenticated, setAuthenticated] = useState(false);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function checkSession() {
    setChecking(true); setError("");
    try {
      const session = await authRequest("session");
      setAuthenticated(session.authenticated === true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to check your session. Please try again.");
    } finally { setChecking(false); }
  }

  useEffect(() => {
    void checkSession();
    const requireLogin = () => {
      resetReviewSession();
      setAuthenticated(false);
      setError("Your session has expired. Sign in again to continue.");
    };
    window.addEventListener("pdf-review-auth-required", requireLogin);
    return () => window.removeEventListener("pdf-review-auth-required", requireLogin);
  }, []);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      await authRequest("login", "POST", {password});
      const session = await authRequest("session");
      if (session.authenticated !== true) throw new Error("Sign-in could not establish a session. Enable cookies for this website and try again.");
      resetReviewSession(); setPassword(""); setAuthenticated(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign in. Please try again.");
    } finally { setBusy(false); }
  }

  async function signOut() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await authRequest("logout", "POST");
      resetReviewSession(); setPassword(""); setAuthenticated(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign out. Please try again.");
    } finally { setBusy(false); }
  }

  return <div className="hosted-workspace">
    {authenticated ? <>
      <div className="hosted-session-bar">
        <div><strong>Private Vercel workspace</strong><span>Documents are processed and stored in your private Vercel workspace.</span></div>
        <button type="button" disabled={busy} onClick={() => void signOut()}>{busy ? "Signing out…" : "Sign out"}</button>
      </div>
      {error && <p className="hosted-auth-error" role="alert">{error}</p>}
      <p className="hosted-quota-notice">Free-plan limits apply. Large documents and long jobs may pause when storage, usage or execution limits are reached. Review extracted text and translations before approving an export.</p>
      <ReviewWorkspace runtime="vercel" />
    </> : <main className="hosted-login-shell">
      <section className="hosted-login-card" aria-labelledby="hosted-title">
        <p className="hosted-eyebrow">PRIVATE DOCUMENT WORKSPACE</p>
        <h1 id="hosted-title">PDF Reader Studio</h1>
        <p>Read PDFs, translate Hindi and English, and review every page before export.</p>
        {checking ? <p role="status">Checking your session…</p> : <>
          <h2>Owner sign-in</h2>
          <p>Enter the workspace password to access your documents.</p>
          <form onSubmit={signIn}>
            <label htmlFor="workspace-password">Workspace password</label>
            <input id="workspace-password" name="password" type="password" autoComplete="current-password" required value={password} disabled={busy} onChange={event => setPassword(event.target.value)} />
            <button type="submit" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
          </form>
          {error && <div className="hosted-auth-error" role="alert"><p>{error}</p><button type="button" disabled={busy} onClick={() => void checkSession()}>Retry session check</button></div>}
        </>}
        <p className="hosted-privacy">Documents are processed and stored in your private Vercel workspace. Unsubmitted review drafts may also be stored in this browser.</p>
      </section>
    </main>}
  </div>;
}
