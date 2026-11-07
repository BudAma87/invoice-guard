"use client";

import * as React from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";

type PdfJs = typeof import("pdfjs-dist");

let loader: Promise<PdfJs> | null = null;

/** Load PDF.js as a native ES module from /public/pdfjs (never bundled; see copy script). */
export function loadPdfJs(): Promise<PdfJs> {
  if (!loader) {
    const url = "/pdfjs/pdf.min.mjs";
    loader = import(/* webpackIgnore: true */ url).then((m: PdfJs) => {
      m.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.mjs";
      return m;
    });
  }
  return loader;
}

export function usePdfDocument(url: string | null) {
  const [doc, setDoc] = React.useState<PDFDocumentProxy | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    setDoc(null);
    setError(null);
    if (!url) return;
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    loadPdfJs()
      .then((pdfjs) => pdfjs.getDocument({ url, isEvalSupported: false }).promise)
      .then((d) => {
        loaded = d;
        if (!cancelled) setDoc(d);
        else d.destroy();
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
      loaded?.destroy();
    };
  }, [url]);
  return { doc, error };
}

/** One page rendered to a canvas at a given CSS width; re-renders on size/rotation change. */
export function PdfPageCanvas({
  doc,
  pageNumber,
  width,
  rotation = 0,
  className,
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  width: number;
  rotation?: number;
  className?: string;
}) {
  const ref = React.useRef<HTMLCanvasElement>(null);
  const [ready, setReady] = React.useState(false);
  React.useEffect(() => {
    let task: RenderTask | null = null;
    let cancelled = false;
    setReady(false);
    doc.getPage(pageNumber).then((page) => {
      if (cancelled || !ref.current) return;
      const base = page.getViewport({ scale: 1, rotation });
      const scale = width / base.width;
      const dpr = window.devicePixelRatio || 1;
      const vp = page.getViewport({ scale: scale * dpr, rotation });
      const canvas = ref.current;
      canvas.width = Math.floor(vp.width);
      canvas.height = Math.floor(vp.height);
      canvas.style.width = `${Math.floor(vp.width / dpr)}px`;
      canvas.style.height = `${Math.floor(vp.height / dpr)}px`;
      task = page.render({ canvas, canvasContext: canvas.getContext("2d")!, viewport: vp });
      task.promise.then(() => !cancelled && setReady(true)).catch(() => {});
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, pageNumber, width, rotation]);
  return <canvas ref={ref} className={className} data-ready={ready || undefined} aria-hidden />;
}
