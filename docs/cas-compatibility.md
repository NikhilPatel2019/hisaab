# CAS compatibility: originals vs unlocked copies

hisaab reads a CAS with [casparser](https://github.com/codereverser/casparser), pinned to
**1.4.1** in `backend/pyproject.toml`. It always reads the **original** file from the vault and passes
the password in memory. It never reads an unlocked copy.

Some setups keep unlocked copies anyway. For example, an older GitHub Action replaced each PDF in Drive
with an unlocked version made by `pikepdf`. Before trusting such copies, check that casparser reads
them **exactly** like the originals.

## Running the check

```bash
cd backend
uv run hisaab cas-compat path/to/original.pdf path/to/unlocked-copy.pdf
# asks for the CAS password (or pipe it with --password-stdin)
```

The check parses both files and compares every value in casparser's output. It prints only a verdict
and, if anything differs, the paths of the fields that differ. It never prints names, folio numbers
or amounts, so the output is safe to paste into an issue.

```
✓ Identical: 412 values, casparser 1.4.1.
```

## Recorded results

| Date | casparser | Issuer | Copy made by | Result |
|---|---|---|---|---|
| _pending_ | 1.4.1 | CDSL | pikepdf (statement-unlocker Action) | _run the check on one of your own CAS and record the verdict here_ |

casparser 1.x refuses re-printed PDFs ("print to PDF"), because flattening removes the issuer's
watermark text. A `pikepdf` unlock only removes the encryption and keeps the content streams, so it is
expected to pass. This table records whether it actually did.
