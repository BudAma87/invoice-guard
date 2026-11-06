// Copy the PDF.js worker next to the app so react-pdf loads it same-origin (CSP-friendly).
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
try {
  const pkg = dirname(require.resolve("pdfjs-dist/package.json", { paths: [require.resolve("react-pdf")] }));
  const src = join(pkg, "build", "pdf.worker.min.mjs");
  if (!existsSync("public")) mkdirSync("public");
  copyFileSync(src, join("public", "pdf.worker.min.mjs"));
  console.log("pdf.worker.min.mjs copied");
} catch (e) {
  console.warn("could not copy pdf worker:", e.message);
}
