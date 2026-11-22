import React, {useEffect, useState} from "react";
import {reviewCall} from "./reviewApi";

interface Memory {available_mb: number; total_mb: number; required_mb: number; can_start_worker: boolean}

export function MemoryStatus() {
  const [memory, setMemory] = useState<Memory>();
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  async function check() {
    setChecking(true); setError("");
    try { setMemory((await reviewCall<{memory: Memory}>("/resources")).memory); }
    catch { setMemory(undefined); setError("Unable to check RAM. Make sure the local server is running."); }
    finally { setChecking(false); }
  }
  useEffect(() => { void check(); }, []);
  return <div className="memory-status">
    <p aria-live="polite">{error || (memory
      ? `Available RAM now: ${memory.available_mb.toLocaleString()} MB of ${memory.total_mb.toLocaleString()} MB. Required before processing: ${memory.required_mb.toLocaleString()} MB.`
      : "Checking available RAM…")}</p>
    {memory && <p>{memory.can_start_worker
      ? "The memory check currently passes. Retry inspection or resume the paused job. Available RAM can change while other applications are open."
      : "Close unused applications or browser tabs, then check again. This is computer memory, not disk space. Your original and saved progress are retained."}</p>}
    <button disabled={checking} onClick={() => void check()}>{checking ? "Checking RAM…" : "Check available RAM"}</button>
  </div>;
}
