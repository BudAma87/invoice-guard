import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const EXP: Record<string, number> = { JPY: 0, KRW: 0, KWD: 3, BHD: 3, OMR: 3 };
export const exponent = (ccy?: string | null) => (ccy ? (EXP[ccy] ?? 2) : 2);

/** Integer minor units -> "1,416.50". Never floats for maths; display only. */
export function money(minor: number | null | undefined, ccy?: string | null, signed = false) {
  if (minor === null || minor === undefined) return "—";
  const e = exponent(ccy);
  const neg = minor < 0;
  const abs = Math.abs(minor);
  const major = Math.floor(abs / 10 ** e);
  const frac = e ? "." + String(abs % 10 ** e).padStart(e, "0") : "";
  const s = major.toLocaleString("en-US") + frac;
  if (neg) return "−" + s;
  return signed && minor > 0 ? "+" + s : s;
}

/** "1,416.50" -> 141650 (minor). Returns null on garbage. */
export function parseMoney(input: string, ccy?: string | null): number | null {
  const cleaned = input.replace(/[,\s]/g, "").replace("−", "-");
  if (!/^-?\d+(\.\d+)?$/.test(cleaned)) return null;
  const e = exponent(ccy);
  const [int, frac = ""] = cleaned.replace("-", "").split(".");
  if (frac.length > e) return null;
  const v = Number(int) * 10 ** e + Number((frac + "0".repeat(e)).slice(0, e) || 0);
  return cleaned.startsWith("-") ? -v : v;
}

export function lkrShort(minor: number | null | undefined) {
  if (minor == null) return "—";
  const v = minor / 100;
  if (v >= 1e6) return `LKR ${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `LKR ${(v / 1e3).toFixed(0)}k`;
  return `LKR ${v.toFixed(0)}`;
}

export function relTime(iso: string, now = Date.now()) {
  const d = new Date(iso).getTime() - now;
  const abs = Math.abs(d);
  const m = Math.round(abs / 60000);
  const h = Math.floor(m / 60);
  const txt = h ? `${h}h ${m % 60}m` : `${m}m`;
  return d >= 0 ? `${txt} left` : `${txt} over`;
}

export function dateTime(iso: string) {
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const REASON_LABEL: Record<string, string> = {
  PERIOD_CLOSED: "Period closed — post in first open period",
  PRICE_AGREED: "Price agreed with supplier",
  QTY_PARTIAL: "Partial delivery / quantity agreed",
  SUPPLIER_CONFIRMED: "Supplier confirmed",
  OCR_CORRECTION: "Corrected misread value",
  DUPLICATE_CONFIRMED: "Duplicate confirmed",
  NOT_OUR_INVOICE: "Not our invoice",
  OTHER: "Other (comment ≥ 20 characters)",
};

export const RULE_LABEL: Record<string, string> = {
  DATE_PARSE: "Date readable",
  DATE_NOT_FUTURE: "Not in the future",
  DATE_AGE: "≤ 90 days old",
  DATE_AFTER_PO: "On/after PO date",
  DATE_PERIOD_OPEN: "Posting period open",
  LINE_ARITH: "Qty × price = amount",
  SUBTOTAL_SUM: "Lines = subtotal",
  TAX_CHECK: "Tax rate & amount",
  TOTAL_RECONCILE: "Total reconciles",
  TOTAL_POSITIVE: "Total positive",
  CURRENCY_MATCH: "Currency matches PO",
  TOTAL_VS_PO: "Within PO balance",
  LINES_PRESENT: "Line items present",
  LINE_PO_MATCH: "Matched to PO line",
  LINE_ITEM_MASTER: "Item in item master",
  LINE_PRICE: "Price within ±2% of PO",
  LINE_QTY: "Qty ≤ open PO qty",
  LINE_UOM: "Unit of measure",
  LINE_REPEAT: "PO line billed once",
  SUPPLIER_RESOLVE: "Supplier resolved",
  SUPPLIER_INACTIVE: "Supplier active",
  PO_OWNERSHIP: "PO open & ours",
  DUP_EXACT: "Not a duplicate",
  DUP_FUZZY: "No near-duplicate",
};

export const GROUP_ORDER = ["identity", "date", "total", "lines"] as const;

export function reasonChip(r: string) {
  if (r.startsWith("LOW_CONF:")) return { code: "LOW_CONF", detail: r.slice(9) };
  return { code: r, detail: null };
}

export function cssId(s: string) {
  return s.replace(/[^a-zA-Z0-9_-]/g, "_");
}
