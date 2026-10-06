# Zoho Payment Reconciliation

A supervised assistant that applies **Payments Received** to **open invoices** in Zoho Books.
It follows the manual workflow:

```
Sales → Payments Received → payment created by Harish with an Unused Amount
→ you confirm the payment → open it → find open invoices → you approve each application
→ Save → re-read Zoho and verify the Unused Amount → repeat until Unused Amount = 0
```

**It never changes anything without you typing `YES`**, and by default it runs in
**DRY RUN** mode, where nothing is ever saved.

---

## Current status - please read

| Part | Status |
|---|---|
| Setup, configuration, logging, error reports | Working and tested |
| Accounting rules (amounts, matching, validation, loop, safety stops) | Working: 148 automated tests |
| Browser start (your Google Chrome), Zoho sign-in page, manual MFA pause | Working, tested against the real Zoho sign-in page |
| Reading the Payments Received list, payment and Edit Payment screens | Written, but **not yet checked against your Zoho screens** |
| LIVE mode (saving) | **Blocked** until the step above is verified |

The automation reads Zoho screens by their visible wording ("Payments Received",
"Unused Amount", "Amount in Excess", ...). That wording has not yet been compared with
**your** Zoho account. Until it has, live mode refuses to start. See
[Verifying the Zoho screens](#verifying-the-zoho-screens).

---

## 1. Requirements

- Windows 10/11 (also works on macOS/Linux)
- Google Chrome (already installed on this computer)
- Internet access and your Zoho Books login
- **uv**, which installs Python and everything else for you (step 2)

## 2. Install uv

Open **PowerShell** and run:

```powershell
winget install --id astral-sh.uv -e
```

Then **close and reopen** your terminal (or VS Code) so the `uv` command is found.
Check it with `uv --version`. (uv is already installed on this computer.)

## 3. Set up the project

In VS Code, open a terminal (**Terminal → New Terminal**). Choose **Git Bash** from the
dropdown next to the `+` and run:

```bash
./scripts/setup.sh
```

This installs Python 3.12, the libraries and the browser, creates `.env`, checks your
configuration and runs the tests. Every step prints `[OK]`, `[WARN]` or `[FAIL]`.

If you prefer PowerShell, run these instead:

```powershell
uv sync
uv run playwright install chromium
uv run pytest -q
```

## 4. Create `.env`

`setup.sh` copies `.env.example` to `.env`. Open `.env` in VS Code and set at least:

```env
ZOHO_BASE_URL=https://books.zoho.com    # use the address YOU log in to (.in, .eu, ...)
PAYMENT_CREATED_BY=Harish
DRY_RUN=true
```

All the settings are explained inside the file. `.env` is private: it is excluded from
git and must never be shared.

## 5. Zoho credentials (optional)

```env
ZOHO_EMAIL=you@company.com
ZOHO_PASSWORD=your-password
```

You can leave both **blank**. The browser then stops on the Zoho sign-in page and you
log in yourself. That is the most secure option. With `PERSIST_BROWSER_SESSION=true`
(the default), the login is remembered on this computer in `data/browser-profile/`,
so you rarely need to log in again.

If Zoho asks for an **OTP, MFA or CAPTCHA**, the program pauses and shows:

```
Manual verification required.
Please complete the Zoho verification in the browser.
Press ENTER after verification is completed.
```

Complete the verification in the browser window, then press ENTER in the terminal.

## 6. Playwright browser

The program uses your installed **Google Chrome** (`BROWSER_CHANNEL=chrome`). If Chrome
cannot start, it falls back to Playwright's Chromium, which `setup.sh` installs.

## 7. Run the tests

```bash
uv run pytest -q
```

Expected result: `148 passed`. The tests never connect to Zoho.

## 8. Start a DRY RUN

```bash
./scripts/run.sh
```

or, in any terminal:

```bash
uv run python -m src.main
```

You will see:

```
========================================
ZOHO PAYMENT RECONCILIATION
========================================

Mode: DRY RUN (nothing will be saved)

Select Marketplace:
1. US  2. UK  3. eBay ... 10. Mexico
```

## 9. Select the marketplace

Type a number (`1`) or a name (`us`, `US` and `Us` all mean US). Unknown entries are
rejected and you are asked again. The program never picks a marketplace for you.

It then logs in, opens **Sales → Payments Received** and lists the payments that:
- have an **Unused Amount greater than 0**,
- belong to the selected marketplace (see [Marketplace rules](#marketplace-rules)),
- were created by `PAYMENT_CREATED_BY`. "Harish" matches "Harish" and "Harish N", but not "Harishankar".

## 10. Approve the payment

If several payments match, choose one by number. You then see the `PAYMENT FOUND`
screen (customer, amounts, reference, date). Only `YES` continues. Anything else means NO.

The payment is then opened and re-read. If any value changed in the meantime, you are
shown the changes and asked again.

## 11. Approve each invoice application

For each open invoice of that customer, you see:

```
PROPOSED PAYMENT APPLICATION
Invoice: INV-1001
Invoice Outstanding: 2,000.00
Payment Unused Before: 4,000.00
Proposed Application: 2,000.00
Payment Unused After: 2,000.00
Type YES or NO (STOP to end):
```

- **YES**: apply this amount (in DRY RUN, it is only simulated)
- **NO**: skip this invoice and propose the next one
- **STOP**: end the run

The amount is always `min(unused amount, invoice outstanding)`, so it never exceeds either.
The run ends when the unused amount reaches 0, when no eligible invoice is left, or after
`MAX_INVOICE_APPLICATIONS`.

An invoice is skipped when its customer or currency differs, or when it is paid, void,
draft or already has money from this payment. If something is **unclear** (for example,
the currency cannot be confirmed or the status is unknown), the run **stops** rather
than guess.

## 12. Enable live mode

Only after [the Zoho screens are verified](#verifying-the-zoho-screens):

1. In `.env`, set `DRY_RUN=false`.
2. Start the program. It asks you to type `YES` to confirm LIVE mode.
3. Every application still needs your `YES`.

For every saved application, the program:
1. types the exact amount into that invoice's row only,
2. checks that **no other row changed** and the payment total and customer are unchanged,
3. checks that Zoho's own *Amount in Excess* equals the expected remaining amount,
4. presses Save and waits for Zoho to close the form,
5. re-reads the payment from the Payments Received list and checks the Unused Amount.

If any check fails, it stops immediately:

```
APPLICATION VERIFICATION FAILED
Expected unused amount: 2,000.00
Actual unused amount: 1,500.00
No further financial actions will be performed.
```

To go back to safe mode, set `DRY_RUN=true`.

## 13. Troubleshooting

| Message | What to do |
|---|---|
| `uv: command not found` | Close and reopen the terminal / VS Code after installing uv. |
| `CONFIGURATION ERROR: ...` | Fix the named setting in `.env`. Check it with `uv run python -m src.main --check-config`. |
| `Manual verification required` | Finish the login, OTP or organization choice in the browser, then press ENTER. |
| `Unable to confidently identify the marketplace` | Update `config/marketplace_rules.json` (below). |
| `The Payments Received table was not found` | The Zoho screen differs from what is expected. Run inspection mode and send the files to whoever maintains the tool. |
| `RECONCILIATION STOPPED` | Read the reason. Details are in `logs/errors/<time>/`: `screenshot.png`, `page.html`, `error.log`, `current_url.txt`. |
| `LIVE MODE IS BLOCKED` | The Zoho screens have not been verified yet (see below). |
| Payment not found in the list | The program reads the **visible** list page. In Zoho, adjust the list view/filter or the number of rows per page. |

Logs are in `logs/reconciliation.log`. Screenshots of key steps are in
`logs/screenshots/` (turn them off with `SAVE_DEBUG_SCREENSHOTS=false`). Passwords,
OTPs, cookies and tokens are never logged.

---

## Marketplace rules

How a marketplace appears on a payment (customer name, reference, ...) is specific to your
business. It is configured in **`config/marketplace_rules.json`**:

```json
"fields_to_search": ["customer", "reference"],
"marketplaces": {
  "US":   { "keywords": ["US"], "case_sensitive": true },
  "eBay": { "keywords": ["eBay"] }
}
```

- A payment belongs to a marketplace when one of its keywords appears as a **whole word**
  in one of the listed fields. For example, "US" matches "Amazon US" but not "USD" or "Business".
- Allowed fields: `customer`, `reference`, `notes`, `deposit_to` (the last two are used
  only if those columns are shown in your Zoho list).
- A payment matching **no** marketplace or **more than one** is never used. The program
  lists such payments and asks for guidance.
- The current keywords are only defaults (the marketplace names). Replace them with your
  real wording, for example `"US": {"keywords": ["Amazon US", "Amazon.com"]}`, then set
  `"reviewed": true`.

## Verifying the Zoho screens

This must be done **once** before live mode, and again if Zoho changes its design.

1. **Inspection mode** (read only; nothing is clicked or saved):
   ```bash
   uv run python -m src.main --inspect
   ```
   Log in. Then, in the browser, open the pages below **yourself**, and for each one type
   a name in the terminal:
   - `payments_list`: Sales → Payments Received
   - `payment_detail`: a Harish payment with an unused amount
   - `edit_payment`: that payment's **Edit** screen with the invoice list. **Do not save.**

   Files are written to `logs/inspection/`. They contain financial data, so share them
   only with the person maintaining this tool.
2. The selectors in `src/zoho_selectors.py` are adjusted to match the captures.
3. Run DRY RUNs for a few marketplaces and check every proposal against Zoho by hand.
4. Only then is `LIVE_UI_VERIFIED = True` set in `src/zoho_selectors.py`.
5. Do the first live applications on small amounts and check them in Zoho.

What must be confirmed:

| Item | Expected (unverified) |
|---|---|
| Menu | "Sales" → "Payments Received" links |
| Payment list columns | "Payment #", "Customer Name", "Amount", "Unused Amount" (+ "Reference Number", "Date") |
| Creator | a "Created By" list column, or a "Created By" label on the payment page |
| Payment page | an "Edit" button |
| Edit form fields | "Customer Name", "Amount Received", "Amount in Excess" |
| Invoice table | "Invoice Number", "Invoice Amount", "Amount Due", and a "Payment" box per row |
| Currency | shown with the invoice amounts (otherwise the run stops as "ambiguous") |
| Save | a single "Save" button. The form closes after a successful save. |

---

## Project structure

```
config/marketplace_rules.json  How marketplaces are recognised (edit this)
scripts/setup.sh, run.sh       Setup and start scripts
src/
  main.py                      Program start, overall flow
  config.py                    Reads and checks .env
  approval.py                  Everything shown in / typed into the terminal
  accounting.py                Pure accounting rules (amounts, matching, validation)
  invoice_reconciliation.py    The apply → save → verify loop with all safety stops
  marketplace.py               Marketplace list, input normalisation, identification
  models.py, exceptions.py     Data types and error types
  logger.py, utils.py          Logging (with redaction), amount parsing
  browser.py                   Starts Chrome, screenshots, error reports
  zoho_selectors.py            ALL knowledge about Zoho's screens (one place)
  page_helpers.py              Reads tables by column heading, values by label
  zoho_login.py                Login + manual MFA pause
  zoho_navigation.py           Sales → Payments Received
  payment_received.py          Payments list and payment page
  invoice_allocation.py        Edit Payment form (the only place amounts are typed)
  zoho_gateway.py              Connects the loop to Zoho; all pre-save checks
  inspector.py                 Inspection mode
tests/                         148 tests: no Zoho account needed
logs/                          Log file, screenshots, error reports (not in git)
data/                          Saved browser login (not in git)
```

## Safety design (summary)

- `DRY_RUN=true` by default. In dry run, the code path that presses Save refuses to run.
- Live mode is refused while the Zoho screens are unverified.
- Every payment and every application needs an explicit `YES`. The approval must match the
  payment, invoice and amount that the program recalculates itself.
- Before Save: only the approved invoice row may have changed, and Zoho's own total must agree.
- After Save: the unused amount is re-read from Zoho and must equal the expected value,
  otherwise everything stops.
- The program never deletes, creates or edits invoices, customers, totals or currencies.
  Its only action is typing an amount into an invoice row of an existing payment and pressing Save.
- The loop is bounded (`MAX_INVOICE_APPLICATIONS`). Anything unexpected stops the run and
  saves diagnostics.
