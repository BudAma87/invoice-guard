// Response shapes of the review API (services/api/views.py).

export type RuleStatus = "PASS" | "WARN" | "FAIL" | "SKIPPED";
export type Severity = "BLOCK" | "FAIL" | "WARN";
export type Group = "identity" | "date" | "total" | "lines";
export type SlaState = "on_track" | "at_risk" | "breached" | "on_hold";

export interface RuleResult {
  key: string;
  code: string;
  status: RuleStatus;
  severity: Severity;
  stage: string;
  group: Group;
  line: number | null;
  expected: unknown;
  actual: unknown;
  message: string;
  fields: string[];
  needs: string | null;
}

export interface Evidence {
  page: number;
  bbox: [number, number, number, number];
  text: string;
}

export interface Header {
  supplier_name: string | null;
  supplier_tax_id: string | null;
  invoice_no: string | null;
  invoice_date: string | null;
  currency: string | null;
  po_number: string | null;
  subtotal_minor: number | null;
  tax_minor: number;
  charges_minor: number;
  discount_minor: number;
  total_minor: number | null;
}

export interface InvoiceLine {
  line_no: number;
  description: string;
  item_code: string | null;
  uom: string | null;
  qty: string | null;
  unit_price_minor: number | null;
  amount_minor: number | null;
  po_line_id: string | null;
  confidence?: Record<string, number>;
  evidence?: Record<string, Evidence>;
}

export interface Task {
  task_id: string;
  kind: "REVIEW" | "CHECK" | "MANUAL_ENTRY";
  status: string;
  reasons: string[];
  priority: number;
  sla_due_at: string;
  sla_ratio: number;
  sla_state: SlaState;
  assignee: string | null;
  assignee_name: string | null;
  lease_until: string | null;
  claimed_by_me: boolean;
  hold_until: string | null;
  hold_note: string | null;
  maker_id: string | null;
  maker_name: string | null;
  decision: string | null;
  reason_code: string | null;
  comment: string | null;
  corrections: Corrections;
  overrides: Overrides;
  results_before: RuleResult[];
  results_after: RuleResult[];
}

export interface QueueItem extends Task {
  invoice_id: string;
  state: string;
  entity_id: string;
  supplier_name: string | null;
  invoice_no: string | null;
  invoice_date: string | null;
  total_minor: number | null;
  currency: string | null;
  amount_lkr_minor: number | null;
}

export interface QueueResponse {
  items: QueueItem[];
  total: number;
  facets: { reasons: string[]; suppliers: string[] };
  next_sla_risk_at: string | null;
}

export interface POCandidate {
  po_line_id: string;
  line_no: number;
  item_code: string;
  description: string;
  price_minor: number;
  open_qty: string;
  uom: string;
  score: number;
}

export interface AuditEvent {
  id: number;
  action: string;
  from: string | null;
  to: string | null;
  actor: string;
  actor_name: string;
  role: string | null;
  at: string;
  payload: Record<string, unknown>;
}

export interface InvoiceDetail {
  invoice_id: string;
  entity: { id: string; name: string };
  state: string;
  version: number;
  supplier_id: string | null;
  header: Header;
  date_ambiguous: boolean;
  confidence: Record<string, number>;
  evidence: Record<string, Evidence>;
  lines: InvoiceLine[];
  rule_set_version: string | null;
  results: RuleResult[];
  results_digest: string;
  amount_lkr_minor: number | null;
  needs_checker: boolean;
  maker_checker_lkr_minor: number;
  min_confidence: number;
  posting_period: string | null;
  period: { period: string; open: boolean; first_open: string | null } | null;
  po: { po_number: string; approved_on: string; remaining_minor: number; currency: string } | null;
  po_line_candidates: { line_no: number; candidates: POCandidate[] }[];
  erp_available: boolean;
  erp_doc_no: string | null;
  last_error: string | null;
  duplicate: {
    invoice_id: string;
    state: string;
    header: Header;
    erp_doc_no: string | null;
    pdf: { url: string } | null;
    lines: InvoiceLine[];
  } | null;
  task: Task | null;
  pdf: { url: string; expires_at: string } | null;
  events: AuditEvent[];
}

export type Corrections = {
  header?: Partial<Record<keyof Header | "invoice_no", unknown>>;
  lines?: Omit<InvoiceLine, "confidence" | "evidence">[];
};
export type Overrides = Record<string, { reason_code: string; comment?: string }>;

export interface ValidateResponse {
  results: RuleResult[];
  diff: { key: string; code: string; from: RuleStatus | null; to: RuleStatus | null }[];
  approve_allowed: boolean;
  problems: string[];
  needs_checker: boolean;
  amount_lkr_minor: number | null;
  erp_available: boolean;
  results_digest: string;
}

export interface Me {
  id: string;
  name: string;
  roles: string[];
  entities: string[];
}

export interface OpsMetrics {
  as_of: string;
  entities: string[];
  month_end_window: boolean;
  tiles: {
    received_today: number;
    auto_pass_rate: number | null;
    queue_depth: number;
    sla_at_risk: number;
    sla_breached: number;
    on_hold: number;
    posted_today: number;
    duplicates_blocked: number;
    provider_error_rate: number;
    oldest_open: { invoice_id: string; age_minutes: number; state: string } | null;
  };
  series: {
    hours: number[];
    arrivals: number[];
    processed: number[];
    reviewer_throughput: number[];
    backlog: number[];
    capacity_per_hour: number;
  };
  queue_by_reason: { reason: string; count: number }[];
}

export interface Overlay {
  field: string;
  label: string;
  evidence: Evidence;
  low: boolean;
}
