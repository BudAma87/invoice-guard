// Serve PDF.js as native ES modules from /public instead of bundling it. Next's webpack
// re-bundling pdfjs-dist v5's pre-built pdf.mjs throws "Object.defineProperty called on
// non-object"; loading the published build unbundled avoids that and keeps it same-origin.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
try {
  const pkg = dirname(require.resolve("pdfjs-dist/package.json"));
  if (!existsSync("public/pdfjs")) mkdirSync("public/pdfjs", { recursive: true });
  for (const f of ["pdf.min.mjs", "pdf.worker.min.mjs"]) {
    copyFileSync(join(pkg, "build", f), join("public", "pdfjs", f));
  }
  console.log("pdfjs copied to public/pdfjs");
} catch (e) {
  console.warn("could not copy pdfjs:", e.message);
}
