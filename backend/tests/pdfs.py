"""Tiny synthetic PDFs for tests: one page of text, optionally password-protected.

Every name, number and institution in these files is made up.
"""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: list[str]) -> bytes:
    body = "BT /F1 10 Tf 40 800 Td 13 TL " + " ".join(f"({_esc(x)}) '" for x in lines) + " ET"
    stream = body.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def lock(pdf: bytes, user_password: str, owner_password: str = "owner-only-x") -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def bank_statement(month: int, year: int = 2025, mask: str = "4321") -> list[str]:
    last = {2: 28, 4: 30, 6: 30, 9: 30, 11: 30}.get(month, 31)
    return [
        "Sample Bank of Nowhere - for tests only",
        "HDFC BANK LIMITED",
        "Statement of Account",
        f"Account No : XXXXXXXX{mask}",
        f"Statement From : 01/{month:02d}/{year} To : {last}/{month:02d}/{year}",
        "Opening Balance 10,000.00   Closing Balance 12,345.00",
    ]


def card_statement(month: int, year: int = 2025, mask: str = "9876") -> list[str]:
    return [
        "Credit Card Statement",
        f"Card Number XXXX XXXX XXXX {mask}",
        f"Statement Period 05 {['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month - 1]} {year} to 04 {['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month % 12]} {year + (1 if month == 12 else 0)}",
        "Total Amount Due 4,560.00   Minimum Amount Due 230.00",
        f"Payment Due Date 24/{(month % 12) + 1:02d}/{year + (1 if month == 12 else 0)}",
    ]


def cas(month: int, year: int = 2025) -> list[str]:
    return [
        "CONSOLIDATED ACCOUNT STATEMENT",
        "Central Depository Services (India) Limited",
        f"for the period 01-{month:02d}-{year} to 28-{month:02d}-{year}",
        "Sample Holder - fictional",
    ]
