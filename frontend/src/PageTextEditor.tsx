import React, {useLayoutEffect, useRef, useState} from "react";
import {Region, ReviewPage} from "./reviewApi";

type Props = {
  page: ReviewPage; regions: Region[]; field: "source" | "translation"; zoom: number;
  edits: Record<string, {source: string; translation: string}>; disabled: boolean;
  onChange: (region: Region, field: "source" | "translation", value: string) => void;
};

function Passage({region, value, field, index, disabled, onChange}: {
  region: Region; value: string; field: Props["field"]; index: number; disabled: boolean;
  onChange: (value: string) => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [overflow, setOverflow] = useState(false);
  const font = region.text_layout?.font_size || region.font_size || 12;
  const lines = Math.max(1, region.source.split("\n").length);
  const [x0, y0, x1, y1] = region.text_layout?.bbox || region.bbox;
  // Older saved pages lack baseline metadata; cap inflated legacy font ascenders.
  const legacy = !region.text_layout && region.fonts?.some(f => /shiva|kruti|chanak|devlys/i.test(f));
  const boxHeight = Math.max(font * 1.35, legacy ? Math.min(y1 - y0, lines * font * 1.5) : y1 - y0);
  const top = legacy ? y1 - boxHeight : y0;
  const width = Math.max(12, x1 - x0 + 2);
  const lineHeight = region.text_layout?.line_height || Math.max(font * 1.25, boxHeight / lines);
  const height = Math.max(boxHeight, lines * lineHeight + 1);
  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    // Font substitution changes glyph widths. Fit without changing any saved text.
    let size = font;
    el.style.fontSize = `${size}px`;
    el.style.lineHeight = `${lineHeight}px`;
    while ((el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) && size > font * .75) {
      size -= .25;
      el.style.fontSize = `${size}px`;
    }
    setOverflow(el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1);
  }, [value, width, height, font, lineHeight]);
  return <div className={`page-text-passage${overflow ? " page-text-overflow" : ""}`} style={{left: x0, top, width, height}}>
    <textarea ref={input} aria-label={`${field === "source" ? "Extracted text" : "Translation"} ${index + 1}`}
      aria-description={overflow ? "Text exceeds its original space. Focus this passage to scroll and edit all of it." : undefined}
      disabled={disabled} value={value} wrap={field === "source" ? "off" : "soft"} spellCheck={false}
      style={{fontSize: font, lineHeight: `${lineHeight}px`, textAlign: region.text_layout?.align || "left",
        fontWeight: region.text_layout?.bold ? 700 : 400, fontStyle: region.text_layout?.italic ? "italic" : "normal"}}
      onChange={e => onChange(e.target.value)}/>
    {overflow && <span className="page-text-overflow-mark" title="More text: click this passage and scroll">↕</span>}
  </div>;
}

export function PageTextEditor({page, regions, field, edits, disabled, zoom, onChange}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  useLayoutEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const scale = width / Math.max(1, page.width) * zoom / 100;
  return <div className="page-text-viewport" ref={container}>
    <div className="page-text-size" style={{width: page.width * scale, height: page.height * scale}}>
      <div className="page-text-sheet" role="group" aria-label={field === "source" ? "Extracted text page" : "Translation page"}
        style={{width: page.width, height: page.height, transform: `scale(${scale})`}}>
        {regions.map(r => <Passage key={r.id} region={r} index={page.regions.findIndex(item => item.id === r.id)}
          field={field} value={edits[r.id]?.[field] ?? r[field]} disabled={disabled}
          onChange={value => onChange(r, field, value)}/>)}
      </div>
    </div>
  </div>;
}
