type Pair = {sourceLanguage: string; targetLanguage: string};
export type Availability = "unavailable" | "downloadable" | "downloading" | "available";
export interface TranslationSession {
  translate(text: string, options?: {signal: AbortSignal}): Promise<string>;
  destroy(): void;
}
type Engine = {
  availability(pair: Pair): Promise<Availability>;
  create(options: Pair & {signal: AbortSignal; monitor: (monitor: EventTarget) => void}): Promise<TranslationSession>;
};

// Other browsers can implement the same API with a different translation engine.
function engine(): Engine | undefined {
  if (!navigator.userAgent.includes("Chrome/") || /Edg\/|OPR\//.test(navigator.userAgent)) return undefined;
  return (window as Window & {Translator?: Engine}).Translator;
}

export async function chromeAvailability(source: string, target: string): Promise<Availability> {
  if (source === target) return "unavailable";
  try {return await engine()?.availability({sourceLanguage: source, targetLanguage: target}) ?? "unavailable";}
  catch {return "unavailable";}
}

export function openChromeTranslator(source: string, target: string, signal: AbortSignal, progress: (text: string) => void) {
  const translator = engine();
  if (!translator) throw new Error("Google’s on-device translator requires a supported desktop Chrome browser.");
  // Start inside the button click. Chrome requires user activation for the initial language pack.
  return translator.create({sourceLanguage: source, targetLanguage: target, signal, monitor(monitor) {
    monitor.addEventListener("downloadprogress", event => {
      const loaded = (event as ProgressEvent).loaded;
      progress(`Preparing Chrome language files… ${Math.round(loaded * 100)}%`);
    });
  }});
}

export async function translateUnits(session: TranslationSession, units: Record<string, string>,
  signal: AbortSignal, progress: (text: string) => void) {
  const result: Record<string, string> = {};
  const items = Object.entries(units);
  for (const [i, [id, text]] of items.entries()) {
    signal.throwIfAborted();
    progress(`Translating passage ${i + 1} of ${items.length} on this device…`);
    const value = await session.translate(text, {signal});
    if (!value.trim()) throw new Error("Chrome returned an empty passage. The page translation could not be saved.");
    result[id] = value;
  }
  return result;
}
