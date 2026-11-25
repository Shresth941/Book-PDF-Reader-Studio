import React, {useLayoutEffect, useRef, useState} from "react";
import {Region, ReviewPage} from "./reviewApi";

type Props = {
  page: ReviewPage; regions: Region[]; field: "source" | "translation"; zoom: number;
  edits: Record<string, {source: string; translation: string}>; disabled: boolean;
  onChange: (region: Region, field: "source" | "translation", value: string) => void;
};

function Passage({region, value, field, index, disabled, onChange, placement, onMeasure}: {
  region: Region; value: string; field: Props["field"]; index: number; disabled: boolean;
  onChange: (value: string) => void;
  placement?: {top: number; height: number}; onMeasure?: (height: number) => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const preview = useRef<HTMLDivElement>(null);
  const [focused, setFocused] = useState(false);
  const [overflow, setOverflow] = useState(false);
  const font = region.text_layout?.font_size || region.font_size || 12;
  const lines = Math.max(1, region.source.split("\n").length);
  const [x0, y0, x1, y1] = region.text_layout?.bbox || region.bbox;
  // Older saved pages lack baseline metadata; cap inflated legacy font ascenders.
  const legacy = !region.text_layout && region.fonts?.some(f => /shiva|kruti|chanak|devlys/i.test(f));
  const boxHeight = Math.max(font * 1.35, legacy ? Math.min(y1 - y0, lines * font * 1.5) : y1 - y0);
  const top = placement?.top ?? (legacy ? y1 - boxHeight : y0);
  const width = Math.max(12, x1 - x0 + 2);
  const lineHeight = region.text_layout?.line_height || Math.max(font * 1.25, boxHeight / lines);
  const height = placement?.height ?? Math.max(boxHeight, lines * lineHeight + 1);
  const geometry = region.text_layout?.lines;
  const lineText = value.split("\n");
  // Geometry belongs to the extracted source. Edited text and translations retain
  // the regular editor rather than being forced onto stale line coordinates.
  const showLines = field === "source" && value === region.text_layout?.source_text && !!geometry && geometry.length === lineText.length && !overflow;
  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    // Font substitution changes glyph widths. Fit without changing any saved text.
    let size = font;
    el.style.fontSize = `${size}px`;
    el.style.lineHeight = `${lineHeight}px`;
    if (field === "translation" && onMeasure) {
      el.style.height = "1px";
      const needed = Math.max(boxHeight, el.scrollHeight + 2);
      el.style.height = "100%";
      onMeasure(needed);
      setOverflow(false);
      return;
    }
    while ((el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) && size > font * .75) {
      size -= .25;
      el.style.fontSize = `${size}px`;
    }
    setOverflow(el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1);
  }, [value, width, height, font, lineHeight, field]);
  useLayoutEffect(() => {
    const nodes = preview.current?.querySelectorAll<HTMLSpanElement>(".page-line-text");
    if (!nodes?.length) return;
    let size = font;
    // Keep one font size per paragraph; expand spaces, never Devanagari letters.
    nodes.forEach(node => {node.style.fontSize = `${font}px`; node.style.wordSpacing = "0px";});
    nodes.forEach(node => {
      if (node.textContent?.includes(" ") || nodes.length === 1)
        size = Math.min(size, font * node.parentElement!.clientWidth / Math.max(1, node.scrollWidth));
    });
    nodes.forEach(node => {
      node.style.fontSize = `${size}px`;
      const spaces = (node.textContent?.match(/ /g) || []).length;
      if (spaces && region.text_layout?.align !== "center") node.style.wordSpacing = `${Math.max(0, node.parentElement!.clientWidth - node.scrollWidth) / spaces}px`;
    });
  }, [showLines, value, geometry, font]);
  return <div className={`page-text-passage${overflow ? " page-text-overflow" : ""}${showLines && !focused ? " page-text-positioned" : ""}`} style={{left: x0, top, width, height}}>
    {showLines && <div ref={preview} className="page-line-preview" aria-hidden="true" style={{visibility: focused ? "hidden" : "visible", fontWeight: region.text_layout?.bold ? 700 : 400, fontStyle: region.text_layout?.italic ? "italic" : "normal"}}>
      {geometry!.map((line, i) => <div key={i} className="page-line" style={{left: line.x - x0, top: line.y - top, width: Math.max(1, line.width), height: lineHeight, textAlign: region.text_layout?.align}}><span className="page-line-text" style={{fontSize: font, lineHeight: `${lineHeight}px`}}>{lineText[i]}</span></div>)}
    </div>}
    <textarea ref={input} aria-label={`${field === "source" ? "Extracted text" : "Translation"} ${index + 1}`}
      aria-description={overflow ? "Text exceeds its original space. Focus this passage to scroll and edit all of it." : undefined}
      disabled={disabled} value={value} wrap={field === "source" ? "off" : "soft"} spellCheck={false}
      onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
      style={{fontSize: font, lineHeight: `${lineHeight}px`, textAlign: region.text_layout?.align || "left",
        fontFamily: field === "translation" && !/[\u0900-\u097f]/.test(value) ? '"Times New Roman", serif' : undefined,
        fontWeight: region.text_layout?.bold ? 700 : 400, fontStyle: region.text_layout?.italic ? "italic" : "normal"}}
      onChange={e => onChange(e.target.value)}/>
    {overflow && <span className="page-text-overflow-mark" title="More text: click this passage and scroll">↕</span>}
  </div>;
}

export function PageTextEditor({page, regions, field, edits, disabled, zoom, onChange}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [measured, setMeasured] = useState<Record<string, number>>({});
  useLayoutEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const scale = width / Math.max(1, page.width) * zoom / 100;
  const positioned: {id: string; x0: number; x1: number; top: number; height: number}[] = [];
  if (field === "translation") {
    for (const r of [...regions].sort((a, b) => (a.text_layout?.bbox || a.bbox)[1] - (b.text_layout?.bbox || b.bbox)[1])) {
      const [x0, y0, x1, y1] = r.text_layout?.bbox || r.bbox;
      const height = measured[r.id] || Math.max(18, y1 - y0);
      const prior = positioned.filter(p => p.x0 < x1 - 2 && p.x1 > x0 + 2);
      const top = Math.max(y0, ...prior.map(p => p.top + p.height + 4));
      positioned.push({id: r.id, x0, x1, top, height});
    }
  }
  const sheetHeight = Math.max(page.height, ...positioned.map(p => p.top + p.height + 36));
  return <div className="page-text-viewport" ref={container}>
    <div className="page-text-size" style={{width: page.width * scale, height: sheetHeight * scale}}>
      <div className="page-text-sheet" role="group" aria-label={field === "source" ? "Extracted text page" : "Translation page"}
        style={{width: page.width, height: sheetHeight, transform: `scale(${scale})`}}>
        {regions.map(r => <Passage key={r.id} region={r} index={page.regions.findIndex(item => item.id === r.id)}
          field={field} value={edits[r.id]?.[field] ?? r[field]} disabled={disabled}
          placement={positioned.find(p => p.id === r.id)}
          onMeasure={field === "translation" ? height => setMeasured(values => values[r.id] === height ? values : {...values, [r.id]: height}) : undefined}
          onChange={value => onChange(r, field, value)}/>)}
      </div>
    </div>
  </div>;
}
