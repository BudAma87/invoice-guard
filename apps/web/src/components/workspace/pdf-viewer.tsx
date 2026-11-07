"use client";

import { RotateCw, ZoomIn, ZoomOut } from "lucide-react";
import * as React from "react";

import { Button, Skeleton } from "@/components/ui/primitives";
import { cn, cssId } from "@/lib/format";
import { PdfPageCanvas, usePdfDocument } from "@/lib/pdfjs";
import type { Overlay } from "@/lib/types";

export type { Overlay };

/**
 * PDF evidence viewer. Clicking a field highlights its box; clicking a box selects the field.
 * Low-confidence regions are outlined amber; the selected field uses the accent colour.
 */
export default function PdfViewer({
  url,
  overlays,
  selected,
  onSelect,
  title,
}: {
  url: string | null;
  overlays: Overlay[];
  selected: string | null;
  onSelect: (field: string) => void;
  title: string;
}) {
  const { doc, error: err } = usePdfDocument(url);
  const pages = doc?.numPages ?? 0;
  const [page, setPage] = React.useState(1);
  const [zoom, setZoom] = React.useState(1);
  const [rot, setRot] = React.useState(0);
  const [width, setWidth] = React.useState(640);
  const wrap = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, e.contentRect.width - 32)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Jump to the page of the selected evidence
  React.useEffect(() => {
    const o = overlays.find((x) => x.field === selected);
    if (o && o.evidence.page !== page) setPage(o.evidence.page);
    if (o) {
      document.getElementById(`ov-${cssId(o.field)}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const pageW = width * zoom;

  return (
    <section aria-label={title} className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2">
        <span className="font-medium">{title}</span>
        <span className="num text-muted">· page {page} of {pages || "…"}</span>
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.6, z - 0.15))}>
            <ZoomOut className="size-4" />
          </Button>
          <span className="num w-11 text-center text-[0.86rem] text-muted">{Math.round(zoom * 100)}%</span>
          <Button size="sm" variant="ghost" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2.5, z + 0.15))}>
            <ZoomIn className="size-4" />
          </Button>
          <Button size="sm" variant="ghost" aria-label="Rotate" onClick={() => setRot((r) => (r + 90) % 360)}>
            <RotateCw className="size-4" />
          </Button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        {pages > 1 ? (
          <nav aria-label="Pages" className="w-20 shrink-0 space-y-2 overflow-auto border-r border-border bg-surface-2 p-2">
            {Array.from({ length: pages }).map((_, i) => (
              <button
                key={i}
                onClick={() => setPage(i + 1)}
                aria-current={page === i + 1 ? "page" : undefined}
                className={cn("block w-full rounded border bg-surface p-1 text-[0.8rem]", page === i + 1 ? "border-accent" : "border-border")}
              >
                {doc ? <PdfPageCanvas doc={doc} pageNumber={i + 1} width={56} /> : null}
                {i + 1}
              </button>
            ))}
          </nav>
        ) : null}
        <div ref={wrap} className="min-h-0 flex-1 overflow-auto bg-surface-3 p-4">
          {!url ? (
            <div className="grid h-full place-items-center text-muted">No PDF stored for this invoice.</div>
          ) : err ? (
            <div className="grid h-full place-items-center text-fail">Could not load PDF: {err}</div>
          ) : !doc ? (
            <Skeleton className="mx-auto aspect-[1/1.414] w-full max-w-[720px]" />
          ) : (
            <>
              <div className="relative mx-auto w-fit bg-white shadow-pop" style={{ transform: `rotate(${rot}deg)` }}>
                <PdfPageCanvas doc={doc} pageNumber={Math.min(page, pages)} width={pageW} className="block" />
                <div className="absolute inset-0">
                  {overlays
                    .filter((o) => o.evidence.page === page)
                    .map((o) => {
                      const [x0, y0, x1, y1] = o.evidence.bbox;
                      if (x1 - x0 <= 0) return null;
                      const isSel = o.field === selected;
                      return (
                        <button
                          key={o.field}
                          id={`ov-${cssId(o.field)}`}
                          onClick={() => onSelect(o.field)}
                          aria-label={`${o.label}: ${o.evidence.text}${o.low ? " (low confidence)" : ""}`}
                          title={`${o.label}${o.low ? " · low confidence" : ""}`}
                          className={cn(
                            "absolute rounded-[3px] border transition-colors",
                            isSel
                              ? "border-2 border-accent bg-accent/15"
                              : o.low
                                ? "border-2 border-warn-line bg-warn-line/15"
                                : "border-transparent hover:border-accent/60 hover:bg-accent/10",
                          )}
                          style={{
                            left: `${(x0 - 0.004) * 100}%`,
                            top: `${(y0 - 0.004) * 100}%`,
                            width: `${(x1 - x0 + 0.008) * 100}%`,
                            height: `${(y1 - y0 + 0.008) * 100}%`,
                          }}
                        >
                          {o.low && !isSel ? (
                            <span className="absolute -top-5 left-0 whitespace-nowrap rounded bg-warn-tint px-1 text-[10px] font-semibold text-warn">
                              low confidence
                            </span>
                          ) : null}
                        </button>
                      );
                    })}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
