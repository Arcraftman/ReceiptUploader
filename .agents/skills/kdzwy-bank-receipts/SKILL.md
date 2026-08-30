---
name: kdzwy-bank-receipts
description: Maintain the receipt-uploader bank workflow, especially per-company/month multi-receipt PDF splitting, bank-key configuration, generated paths, and safe progression toward OCR and voucher upload.
---

# Kdzwy Bank Receipts

Use this skill when changing or explaining the project's bank receipt pipeline.

## Split contract

- Read every bank only from the month `project.json` at `sources.bank.banks`; do not generate, read, or synchronize a separate `bank_split.json`.
- Require `sources.bank.exceptions` to be one unique array of non-empty names. Each value is the exact, case-sensitive text from the configured statement counterparty-name column for that month. Users only enter names; never require group, role, handling, template, record-allocation, or PDF-keyword fields in the month project.
- Seed a newly created company month from the `exceptions` array in `config/bank_exception.defaults.json` only when that month has no `sources.bank.exceptions`. Never merge later default changes into an existing month. Keep only cross-company names such as TIPS in the global seed; company/month-specific names remain in that month's `project.json`. Technical rules for associating split-time bank exception PDFs live only in the global default file's `pdf_keywords` map and are not user-facing month configuration.
- Treat `sources.bank.banks` as the single source of truth. Each lowercase bank key corresponds to both `input/bank/<bank_key>.pdf` and `input/bank/<bank_key>.xlsx` and contains exactly one numeric-string `bank_account_number`, one `split` object, and one `statement_columns` object.
- The dedicated `bank <dataset> <month>` / `run_bank.bat` entry must only read and validate project configuration. It must not add banks, enable the source, change mode/stage, or write any project field. It follows the configured lifecycle: OCR stage splits, separates exceptions, OCRs only the remainder, and matches only the remainder; LLM stage analyzes ordinary matched records; only prepare+existing may generate final receipts.
- Support any number of configured bank keys. Require each `split` object to contain exactly `parts_per_page`, `filename_index_length`, and case-sensitive `filename_index_prefix`.
- Require each `statement_columns` object to contain exactly `index_column`, `bank_debit_column`, `bank_credit_column`, and `counterparty_name_column`; allow `null` only while bank is disabled, and require all four real columns before bank can run.
- Fix `configCompany` to the month project's `dataset.company_name`; never let OCR or an LLM select or overwrite it. A valid amount in the bank debit column is our credit/cash outflow, and the configured counterparty-name cell is a supplier. A valid amount in the bank credit column is our debit/cash inflow, and that cell is a customer. The opposite amount column may be zero or text. The statement cell is authoritative and OCR/LLM must not overwrite the name or role.
- Inject the current bank's `bank_account_number` into its map values, LLM candidate context, and fixed prompt rules. Every bank template must contain exactly one entry whose account-selector name contains `银行存款`; resolve that entry with the configured number instead of the template's historical sample number. Persist the required number in analysis and reject missing runtime accounts, mismatched existing analysis, or final receipts using a different bank account number.
- Resolve every template account by its configured account number. Differences between the template account name and the target accountbook's display/detail name do not block analysis. The explicitly configured bank account number remains authoritative for the one bank-deposit entry.
- Require the source PDF at `input/bank/<bank_key>.pdf`.
- Split every page into equal-height sections using the configured count. Never infer the count from bank text or OCR.
- After cropping, identify the filename index in fast mode: use embedded PDF text first, then RapidOCR at 150 DPI only when needed.
- Enforce filename priority: labelled `交易流水号`/`交易流水`/`核心流水号`, then labelled `回单编号`/equivalent receipt-number labels, then a standalone alphanumeric token. Every accepted candidate must match that bank's exact `filename_index_length` and case-sensitive starting `filename_index_prefix`; preserve the recognized index's original letter casing in the output filename.
- Write recognized one-page PDFs to `generated/bank_receipts/<bank_key>/<filename_index>.pdf`.
- Put every segment without a unique valid naming index, including duplicate-number segments, under `generated/bank_receipts/<bank_key>/bank_exception/`. Treat these as bank exceptions immediately; they are normal deterministic split results and must not fail preprocessing or enter ordinary OCR.
- After every configured bank finishes splitting and before ordinary OCR, run one exception prefilter from `sources.bank.exceptions`. First apply technical `pdf_keywords` rules from `config/bank_exception.defaults.json` only for names present in the month array; for a split-time electronic-tax exception PDF, associate it only when accounting date plus amount yields exactly one configured statement row. Then use the configured statement index column to locate every remaining listed-name PDF. Add any still-unassociated split-time exception PDF to the same authoritative exception manifest.
- Copy every special PDF to `generated/bank_exceptions/<counterparty>/<bank_key>__<index>.pdf` while preserving its original split file. Write one authoritative `generated/maps/bank/bank_exceptions.json` containing both source and copied paths plus exact excluded statement indexes and PDF paths. Missing or ambiguous associations remain explicit in the manifest.
- Start bank OCR only after the exception prefilter. Exclude every split-time bank exception, exact configured special PDF path, and configured statement index from ordinary OCR and matching, and remove stale OCR artifacts for those paths. Continue OCR only for ordinary indexed receipts, using embedded PDF text first and RapidOCR at 300 DPI when needed.
- Write bank OCR artifacts to `generated/ocr/bank/<bank_key>/<receipt>/ocr.txt` and `ocr.json`, plus `generated/ocr/bank/ocr_stage.report.json`; cache only unchanged successful artifacts.
- After OCR, match each recognized receipt filename index case-sensitively to the configured index column in `input/bank/<bank_key>.xlsx`. Write exactly one grouped `generated/maps/bank/bank_map.json` plus `bank_map.report.json`; do not create duplicate per-bank map files.
- Match only the remaining OCR artifacts and statement rows. Special objects never enter the ordinary `bank_map.json`, LLM input, template selection, or ordinary receipt generation.
- Map a nonzero bank debit amount to our credit/cash outflow and a nonzero bank credit amount to our debit/cash inflow. Treat the opposite column as inactive when it is zero, empty, or non-amount text (including long numeric identifiers).
- For cash inflow, when the inactive bank-debit cell consists entirely of one or more 8–20 digit numeric strings separated only by whitespace or punctuation, preserve those strings in order as `invoiceNumbers` and directly replace `explanation_body` with the space-joined numbers. Do not append them to the template body and do not trigger this rule when the cell contains any ordinary text.
- Extract the transaction/accounting date deterministically from each matched receipt's OCR text. Exactly the one template entry whose selector name contains `银行存款` must end its explanation with one space plus that `YYYY-MM-DD` date; counterpart entries keep the base explanation without the date. Persist per-entry explanations and reject existing analysis or final receipt preparation when the date/body rules are missing or stale.
- Require every bank template to declare `matchRules.flowDirections`. Filter candidates by the statement-derived `flowDirection` before keywords or any model call; an inflow template can never process outflow and vice versa. When rules leave exactly one candidate, select it deterministically and do not call Qwen. Bank validation uses `documentBlock=银行`, `amountSource=source`, the configured source folder, and the fixed flow direction instead of invoice-only folder/map metadata.
- Treat exact foreign-currency markers only (`USD`, `US$`, `HKD`, `EUR`, or their Chinese names); never let substrings such as `USB` trigger foreign-currency rejection. Default inflow/outflow routing must reject a counterparty equal to `configCompany`, leaving internal transfers blocked.
- If the month bank source explicitly sets `preload_items="once"` or `"auto"`, collect customer/supplier names from the matched statement map according to the fixed direction roles and create missing target-accountbook auxiliary items before analysis. This is a remote write and must remain off unless the user intentionally enables it.
- Keep unmatched statement rows as marker-only report entries with `markerOnly=true` and `downstreamEligible=false`. Both split-time and configured special objects are exposed through `exceptions <dataset> <month>` and removed from the user-facing ordinary unmatched list; `unmatched` shows only records not claimed by the exception map. Reject duplicate indexes and ambiguous debit/credit directions before downstream voucher work.
- Treat every exact name in `sources.bank.exceptions` identically and as authoritative regardless of statement direction. Copy its PDF into the special directory and exclude it before ordinary OCR. For an unconfigured name, retain the conservative 2–4 Chinese-character surname rule and report it as `skippedPersonNameStatements` without downstream eligibility.
- Never generate receipt.json during split, OCR, matching, or LLM analysis. After the user reviews `template_analysis.json`, require `mode=prepare` plus `analysis_stage=existing` to generate final-shape receipt files only for valid matched records with analysis. Preserve existing receipt files rather than overwriting user edits.
- Keep every prepare+existing receipt at `draft=true`. `verify <dataset> <month>` is valid only after this final generation stage; it lists each remaining draft number/receiptId/path and validates every `draft=false` receipt. Run the same verification automatically immediately before dry-run or any future real bank upload, blocking the whole submission if any draft or invalid receipt remains.
- Preserve source PDFs. Refresh generated outputs only for the same bank key when its source fingerprint, configured count, or filename-index configuration changes.
- Reject missing PDFs, invalid keys, invalid counts, and unreadable PDFs instead of silently falling back.

## Boundaries

- Do not reuse the reference script's hard-coded year, aggressive amount guessing, date-based renaming, or keyword-based split selection.
- Cropping, plain-text bank OCR, index-based statement matching, LLM analysis wiring, and prepare+existing final draft generation are available. Do not claim bank batch readiness or live upload are complete until generated outputs and responsibility-chain upload rules are separately reviewed.
- Keep bank artifacts isolated from sales and purchase under their own generated directories.

## Project references

- Core splitter: `src/kdzwy_receipt_uploader/bank_receipt_splitter.py`
- Statement matcher: `src/kdzwy_receipt_uploader/bank_statement_matcher.py`
- Special-object filter: `src/kdzwy_receipt_uploader/bank_exception_filter.py`
- Pipeline entry: `src/kdzwy_receipt_uploader/pipeline_runner.py`
- Path defaults: `config/pipeline.defaults.json`
- Unified month configuration: `data/inbox/<dataset>/<month>/project.json`
- Durable decisions: `docs/PROJECT_MEMORY.md`
