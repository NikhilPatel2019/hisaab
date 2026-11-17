"""What is this document? Bank or card statement, CAS, contract note, tax form, payslip…

Your own filing rules are checked first, then the built-in ones. Built-in rules look at who
sent it, its file name and the text of its first pages, so they work for the statements most
Indian banks, card issuers, depositories and brokers send. When nothing matches, the document
waits in the "needs you" queue.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from hisaab.models import Account, DocRule

# (name, sender domains, text patterns). Order matters a little: card issuers that are
# also banks are told apart by kind, not by institution.
INSTITUTIONS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "CDSL",
        ("cdslindia.com", "cdslindia.co.in", "cdslstatement.com"),
        (r"\bCDSL\b", r"Central Depository Services"),
    ),
    (
        "NSDL",
        ("nsdl.co.in", "nsdl.com", "nsdlcas.nsdl.com"),
        (r"\bNSDL\b", r"National Securities Depository"),
    ),
    ("CAMS", ("camsonline.com",), (r"\bCAMS\b", r"Computer Age Management")),
    ("KFintech", ("kfintech.com", "karvy.com"), (r"KFin\s*Tech", r"\bKARVY\b")),
    ("Zerodha", ("zerodha.com", "zerodha.net"), (r"\bZerodha\b",)),
    ("Groww", ("groww.in",), (r"\bGroww\b", r"Nextbillion Technology")),
    ("Upstox", ("upstox.com",), (r"\bUpstox\b", r"RKSV Securities")),
    (
        "Income Tax Department",
        ("incometax.gov.in", "tdscpc.gov.in"),
        (r"Income\s*Tax\s*Department", r"TRACES"),
    ),
    ("SBI Card", ("sbicard.com",), (r"\bSBI\s*Card\b",)),
    ("American Express", ("americanexpress.com", "aexp.com"), (r"American\s*Express",)),
    ("OneCard", ("getonecard.app",), (r"\bOneCard\b",)),
    ("HDFC Bank", ("hdfcbank.net", "hdfcbank.com", "hdfcbank.bank.in"), (r"HDFC\s*Bank",)),
    ("ICICI Bank", ("icicibank.com", "icicibank.bank.in"), (r"ICICI\s*Bank",)),
    (
        "State Bank of India",
        ("sbi.co.in", "onlinesbi.sbi", "sbi.bank.in"),
        (r"State\s*Bank\s*of\s*India",),
    ),
    ("Axis Bank", ("axisbank.com", "axis.bank.in"), (r"Axis\s*Bank",)),
    ("Kotak Mahindra Bank", ("kotak.com", "kotak.bank.in"), (r"Kotak\s*Mahindra",)),
    ("IDFC FIRST Bank", ("idfcfirstbank.com", "idfcfirst.bank.in"), (r"IDFC\s*FIRST",)),
    ("Yes Bank", ("yesbank.in",), (r"\bYES\s*BANK\b",)),
    ("IndusInd Bank", ("indusind.com",), (r"IndusInd",)),
    ("AU Small Finance Bank", ("aubank.in",), (r"AU\s*Small\s*Finance",)),
    ("Federal Bank", ("federalbank.co.in",), (r"Federal\s*Bank",)),
    ("RBL Bank", ("rblbank.com",), (r"\bRBL\s*Bank\b",)),
    (
        "Bank of Baroda",
        ("bankofbaroda.com", "bankofbaroda.co.in", "bobcard.co.in"),
        (r"Bank\s*of\s*Baroda", r"\bBOBCARD\b"),
    ),
    ("Punjab National Bank", ("pnb.co.in", "pnb.bank.in"), (r"Punjab\s*National\s*Bank",)),
    ("Canara Bank", ("canarabank.com", "canarabank.in"), (r"Canara\s*Bank",)),
    (
        "Union Bank of India",
        ("unionbankofindia.co.in", "unionbankofindia.bank.in"),
        (r"Union\s*Bank\s*of\s*India",),
    ),
    ("Standard Chartered", ("sc.com",), (r"Standard\s*Chartered",)),
    ("HSBC", ("hsbc.co.in", "hsbc.com"), (r"\bHSBC\b",)),
    ("Citibank", ("citi.com", "citibank.co.in"), (r"\bCitibank\b",)),
)

KNOWN_SENDER_DOMAINS = tuple(d for _, domains, _ in INSTITUTIONS for d in domains)

# (rule name, kind, text patterns, filename patterns, sender domains). All text patterns are
# case-insensitive; a rule matches when any of its patterns does.
KINDS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "cas",
        "cas",
        (r"Consolidated\s+Account\s+Statement", r"\bCAS\b.*(?:CDSL|NSDL|CAMS|KFin)"),
        (r"\bcas\b", r"consolidated"),
        ("cdslstatement.com", "nsdl.co.in", "nsdl.com", "camsonline.com", "kfintech.com"),
    ),
    (
        "tax",
        "tax",
        (
            r"Annual\s+Information\s+Statement",
            r"Form\s*(?:No\.?\s*)?26\s*AS",
            r"FORM\s+NO\.?\s*16\b",
            r"Taxpayer\s+Information\s+Summary",
        ),
        (r"\bais\b", r"26as", r"form[\s_-]*16", r"\btis\b"),
        ("incometax.gov.in", "tdscpc.gov.in"),
    ),
    (
        "broker",
        "broker",
        (
            r"Contract\s+Note",
            r"Tax\s+P\s*&?\s*L",
            r"Tradewise\s+Exits",
            r"Funds\s+Statement",
            r"Retention\s+Statement",
        ),
        (r"contract[\s_-]*note", r"\bcn\b", r"tax[\s_-]*p&?l", r"tradebook"),
        ("zerodha.com", "zerodha.net", "groww.in", "upstox.com"),
    ),
    (
        "card_statement",
        "card_statement",
        (
            r"Credit\s+Card\s+Statement",
            r"Minimum\s+Amount\s+Due",
            r"Total\s+Amount\s+Due",
            r"Payment\s+Due\s+Date",
            r"Card\s+Statement",
        ),
        (r"credit[\s_-]*card", r"\bcc[\s_-]*statement", r"card[\s_-]*statement"),
        ("sbicard.com", "americanexpress.com", "aexp.com", "getonecard.app", "bobcard.co.in"),
    ),
    (
        "payslip",
        "payslip",
        (r"Pay\s*slip", r"Salary\s+Slip", r"Earnings.*Deductions.*Net\s+Pay"),
        (r"pay[\s_-]*slip", r"salary[\s_-]*slip"),
        (),
    ),
    (
        "insurance",
        "insurance",
        (r"Policy\s+Schedule", r"Premium\s+(?:Paid\s+)?(?:Receipt|Certificate)", r"Sum\s+Assured"),
        (
            r"policy[\s_-]*(?:schedule|document|copy|certificate)",
            r"premium[\s_-]*(?:receipt|certificate|paid)",
        ),
        (),
    ),
    (
        "bank_statement",
        "bank_statement",
        (
            r"Statement\s+of\s+Account",
            r"Account\s+Statement",
            r"Opening\s+Balance.*Closing\s+Balance",
            r"Savings\s+Account",
            r"Current\s+Account",
        ),
        (r"statement", r"\bacct?\b", r"\bsoa\b"),
        (),
    ),
)

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_D = (
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?[\s/.,-]*(?:\d{1,2}|[A-Za-z]{3,9})[\s/.,-]*\d{2,4})"
)
_PERIOD = re.compile(_D + r"\s*(?:to|till|until|through|-|–|—)\s*:?\s*" + _D, re.IGNORECASE)
_MASK = re.compile(
    r"(?:[Xx*•]{2,}[\s-]*)+(\d{4})\b|(?:ending(?:\s+with)?|last\s+4\s+digits?)\s*[:\-]?\s*(\d{4})\b",
    re.IGNORECASE,
)


@dataclass
class Classification:
    kind: str = "unknown"
    institution: str | None = None
    account_id: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    classified_by: str | None = None


def sender_domain(sender: str | None) -> str | None:
    if not sender:
        return None
    m = re.search(r"@([A-Za-z0-9.-]+)", sender)
    return m.group(1).lower().rstrip(".") if m else None


def _domain_matches(domain: str | None, domains: Iterable[str]) -> bool:
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)  # type: ignore[union-attr]


def is_known_sender(sender: str | None, extra: Iterable[str] = ()) -> bool:
    return _domain_matches(sender_domain(sender), (*KNOWN_SENDER_DOMAINS, *extra))


def detect_institution(
    sender: str | None, text: str | None, filename: str | None = None
) -> str | None:
    domain = sender_domain(sender)
    for name, domains, _ in INSTITUTIONS:
        if _domain_matches(domain, domains):
            return name
    hay = text or ""
    for name, _, patterns in INSTITUTIONS:
        if any(re.search(p, hay, re.IGNORECASE) for p in patterns):
            return name
    if filename:
        for name, _, patterns in INSTITUTIONS:
            if any(re.search(p, filename, re.IGNORECASE) for p in patterns):
                return name
    return None


def _parse_date(token: str) -> date | None:
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", token.strip(), flags=re.IGNORECASE)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        parts = [p for p in re.split(r"[\s/.,-]+", t) if p]
        if len(parts) != 3:
            return None
        d_s, mo_s, y_s = parts
        if not d_s.isdigit() or not y_s.isdigit():
            return None
        d, y = int(d_s), int(y_s)
        mo = int(mo_s) if mo_s.isdigit() else _MONTHS.get(mo_s.lower(), 0)
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def find_period(text: str | None) -> tuple[date | None, date | None]:
    for m in _PERIOD.finditer(text or ""):
        a, b = _parse_date(m.group(1)), _parse_date(m.group(2))
        if a and b and a <= b and (b - a).days <= 400 and 2000 <= a.year <= 2100:
            return a, b
    return None, None


def find_masks(text: str | None) -> list[str]:
    out: list[str] = []
    for m in _MASK.finditer(text or ""):
        digits = m.group(1) or m.group(2)
        if digits not in out:
            out.append(digits)
    return out


_KIND_FOR_ACCOUNT = {
    "bank_statement": ("bank",),
    "card_statement": ("credit_card",),
    "cas": ("investment",),
    "broker": ("investment",),
}


def match_account(
    kind: str, text: str | None, institution: str | None, accounts: Sequence[Account]
) -> int | None:
    live = [a for a in accounts if not a.archived]
    masks = find_masks(text)
    by_mask = [a for a in live if a.mask and a.mask in masks]
    wanted = _KIND_FOR_ACCOUNT.get(kind)
    if wanted:
        typed = [a for a in by_mask if a.kind in wanted]
        if len(typed) == 1:
            return typed[0].id
    if len(by_mask) == 1:
        return by_mask[0].id
    if wanted and institution:
        same = [
            a
            for a in live
            if a.kind in wanted and a.institution and _same_institution(a.institution, institution)
        ]
        if len(same) == 1:
            return same[0].id
    return None


def _same_institution(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"\b(bank|ltd|limited|of|india)\b|[^a-z]", "", s.lower())

    return bool(norm(a)) and norm(a) == norm(b)


def _rule_matches(
    rule: DocRule, sender: str | None, filename: str | None, text: str | None, subject: str | None
) -> bool:
    checks = []
    if rule.match_sender:
        checks.append(rule.match_sender.lower() in (sender or "").lower())
    if rule.match_filename:
        pat = rule.match_filename.lower()
        name = (filename or "").lower()
        checks.append(fnmatch.fnmatch(name, pat) if any(c in pat for c in "*?[") else pat in name)
    if rule.match_text:
        needle = rule.match_text.lower()
        checks.append(needle in (text or "").lower() or needle in (subject or "").lower())
    return bool(checks) and all(checks)


def classify(
    *,
    text: str | None,
    sender: str | None,
    filename: str | None,
    subject: str | None = None,
    rules: Sequence[DocRule] = (),
    accounts: Sequence[Account] = (),
) -> Classification:
    out = Classification()
    out.period_start, out.period_end = find_period(text)
    domain = sender_domain(sender)

    for rule in rules:
        if _rule_matches(rule, sender, filename, text, subject):
            out.kind = rule.kind
            out.institution = rule.institution or detect_institution(sender, text, filename)
            out.account_id = rule.account_id or match_account(
                out.kind, text, out.institution, accounts
            )
            out.classified_by = f"rule:{rule.id}"
            return out

    out.institution = detect_institution(sender, text, filename)
    hay_text = text or ""
    hay_name = f"{filename or ''} {subject or ''}"
    for name, kind, text_pats, name_pats, domains in KINDS:
        by_text = any(re.search(p, hay_text, re.IGNORECASE | re.DOTALL) for p in text_pats)
        by_name = any(re.search(p, hay_name, re.IGNORECASE) for p in name_pats)
        by_sender = _domain_matches(domain, domains)
        # Text is strongest. A file name alone isn't enough for the generic "statement"
        # rule unless the sender is a known institution.
        if by_text or by_sender or (by_name and (name != "bank_statement" or out.institution)):
            out.kind = kind
            out.classified_by = f"builtin:{name}"
            break

    if out.kind != "unknown":
        out.account_id = match_account(out.kind, text, out.institution, accounts)
    return out
