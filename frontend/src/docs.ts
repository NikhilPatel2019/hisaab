/** Labels and small helpers for documents in the Postbox. */
import type { Doc, DocKind } from "./api/client";
import { monthLabel, shortDate, todayISO } from "./format";

export const DOC_KINDS: Record<DocKind, string> = {
  bank_statement: "Bank statement",
  card_statement: "Card bill",
  cas: "Investments summary (CAS)",
  broker: "Broker statement",
  tax: "Tax document",
  payslip: "Payslip",
  insurance: "Insurance",
  other: "Other",
  unknown: "Not sorted yet",
};

const SHORT: Record<DocKind, string> = {
  bank_statement: "statement",
  card_statement: "card bill",
  cas: "CAS",
  broker: "broker statement",
  tax: "tax document",
  payslip: "payslip",
  insurance: "insurance",
  other: "document",
  unknown: "document",
};

export const DOC_CODES: Record<DocKind, string> = {
  bank_statement: "BNK",
  card_statement: "CRD",
  cas: "CAS",
  broker: "BRK",
  tax: "TAX",
  payslip: "PAY",
  insurance: "INS",
  other: "DOC",
  unknown: "?",
};

const SOURCES: Record<string, string> = { upload: "Uploaded", drive: "Google Drive", imap: "Email", folder: "Folder" };

/** "Salary account · Oct", "HDFC Bank card bill · Mar 2025", "Not sorted yet". */
export function docTitle(d: Pick<Doc, "kind" | "account_name" | "institution" | "period_end">, today = new Date()): string {
  let base: string;
  if (d.kind === "unknown") base = DOC_KINDS.unknown;
  else if (d.account_name) base = d.account_name;
  else if (d.institution) base = `${d.institution} ${SHORT[d.kind]}`;
  else base = DOC_KINDS[d.kind];
  if (!d.period_end) return base;
  const year = Number(d.period_end.slice(0, 4));
  return `${base} · ${monthLabel(d.period_end.slice(0, 7), year !== today.getFullYear())}`;
}

/** "Statements <x@bank.example>" → "bank.example"; falls back to how it arrived. */
export function docFrom(d: Pick<Doc, "institution" | "sender" | "source">): string {
  if (d.institution) return d.institution;
  const m = d.sender?.match(/@([A-Za-z0-9.-]+)/);
  if (m) return m[1].toLowerCase();
  return SOURCES[d.source] ?? d.source;
}

/** "A document from hdfcbank.net", or how it arrived when there's no sender. */
export function arrivedFrom(d: Pick<Doc, "institution" | "sender" | "source">): string {
  if (d.institution || d.sender) return `A document from ${docFrom(d)}`;
  if (d.source === "upload") return "A file you uploaded";
  if (d.source === "folder") return "A file from your folder";
  return `A file from ${sourceLabel(d.source)}`;
}

export function sourceLabel(source: string): string {
  return SOURCES[source] ?? source;
}

/** The local calendar date of an API timestamp, as "17 Nov". */
export function receivedOn(d: Pick<Doc, "received_at" | "created_at">, today = new Date()): string {
  return shortDate(todayISO(new Date(d.received_at ?? d.created_at)), today);
}

export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function senderDomain(sender: string | null | undefined): string | null {
  const m = sender?.match(/@([A-Za-z0-9.-]+)/);
  return m ? m[1].toLowerCase() : null;
}
