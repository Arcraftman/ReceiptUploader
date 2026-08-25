---
name: kdzwy-bank-receipts
description: Maintain the receipt-uploader bank workflow, especially per-company/month multi-receipt PDF splitting, bank-key configuration, generated paths, and safe progression toward OCR and voucher upload.
---

# Kdzwy Bank Receipts

Use this skill when changing or explaining the project's bank receipt pipeline.

## Split contract

- Read `data/inbox/<dataset>/<month>/input/bank/bank_split.json`.
- Treat it as a direct JSON mapping from an English lowercase bank key to the number of vertically stacked receipts on each PDF page.
- Require the source PDF at `input/bank/<bank_key>.pdf`.
- Split every page into equal-height sections using the configured count. Never infer the count from bank text or OCR.
- After cropping, identify the receipt number in fast mode: use embedded PDF text first, then RapidOCR at 150 DPI only when needed.
- Accept only explicitly labelled invoice, receipt, transaction, voucher, business, or reference numbers; a standalone 20-digit invoice number is the only unlabelled fallback.
- Write recognized one-page PDFs to `generated/bank_receipts/<bank_key>/<receipt_number>.pdf`.
- Put unrecognized or duplicate-number segments under `generated/bank_receipts/<bank_key>/unrecognized/` and block later bank stages.
- Preserve source PDFs. Refresh generated outputs only for the same bank key when its source fingerprint or configured count changes.
- Reject missing PDFs, invalid keys, invalid counts, and unreadable PDFs instead of silently falling back.

## Boundaries

- Do not reuse the reference script's hard-coded year, aggressive amount guessing, date-based renaming, or keyword-based split selection.
- Cropping is preprocessing only. Do not claim bank OCR, transaction matching, voucher templates, or live upload are complete until those stages are separately implemented and reviewed.
- Keep bank artifacts isolated from sales and purchase under their own generated directories.

## Project references

- Core splitter: `src/kdzwy_receipt_uploader/bank_receipt_splitter.py`
- Pipeline entry: `src/kdzwy_receipt_uploader/pipeline_runner.py`
- Path defaults: `config/pipeline.defaults.json`
- Example mapping: `config/bank_split.example.json`
- Durable decisions: `PROJECT_MEMORY.md`
