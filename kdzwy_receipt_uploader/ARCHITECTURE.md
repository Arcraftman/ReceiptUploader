# Kdzwy Receipt Uploader Architecture

## Design goal

This project is organized as a Python application with stable orchestration:
`accountbook <-> dataset <-> job -> company/month pipeline`, and a single set of semantic business sources (`sales`, `purchase`, `bank`, `misc`).

## Stable layers

```text
kdzwy_receipt_uploader/
├─ src/kdzwy_receipt_uploader/       # reusable application package
│  ├─ api.py                         # HTTP transport and response handling
│  ├─ accountbook_resolver.py        # live group/account resolution
│  ├─ auxiliary_items.py             # live ItemClass item lists and matching
│  ├─ matching.py                    # XLSX/PDF invoice matching
│  ├─ sales_map.py                   # sales-side business aggregation
│  ├─ purchase_map.py                # purchase-side business aggregation
│  ├─ source_profile.py              # single source of truth for source kinds
│  ├─ pipeline_runner.py             # single-job stage orchestration
│  ├─ voucher_templates.py           # template selection/rendering
│  ├─ receipt_generation.py          # receipt materialization
│  ├─ workflow.py                    # validation and five/six-step upload workflow
│  └─ cli.py                         # batch orchestration and resumable serial upload
├─ config/                           # non-secret runtime and template configuration
│  ├─ accountbooks.json             # target account-book identity and session binding
│  ├─ datasets.json                 # source legal entity, data root and business overrides
│  ├─ companies/                    # per-company source, month, mode and enablement
│  └─ pipeline.defaults.json         # shared single-job pipeline defaults
├─ schema/                           # receipt JSON schema
├─ tests/                            # local and fake-API tests
├─ data/inbox/                       # business inputs and month-local generated artifacts
├─ runtime/                          # generated jobs, logs and upload lifecycle state
├─ examples/                         # safe, non-production examples
├─ scripts/maintenance/              # optional maintenance and generation tools
├─ login_http.ps1                    # pure-HTTP single/multi-company authentication
├─ run_companies.py                  # multi-company serial orchestration and isolation
├─ start_http_login.bat              # interactive authentication launcher
└─ start_pipeline.bat                # safe analysis-only launcher
```

## Runtime pipeline

```text
configuration
  -> company registry + enabled job queue
  -> isolated company/month run and app configs
  -> month-local XLSX/PDF matching
  -> sales_map / purchase_map aggregation
  -> live account context resolution
  -> live ItemClass item resolution
  -> template selection and receipt generation
  -> dry-run validation
  -> serial confirmed upload
  -> voucher/PDF readback verification
  -> result logging and archival
```

## Four-source responsibility chain and runtime selector

The source selector now uses semantic values only:

- `sales`: 销项凭证（销售发票）。
- `purchase`: 进项凭证（进项发票/采购）。
- `bank`: 银行凭证。
- `misc`: 杂项凭证。
- `all`: sales → purchase → bank → misc。

`bank` and `misc` 目前仍保留阻断策略（未实现完全业务封装前不会自动上传）。

## Safety invariants

- Negative amounts are valid business values, including red invoices.
- Dynamic IDs must come from the current logged-in account book; do not copy IDs from old receipts.
- A confirmed upload is serial and resumable; submitted records are durable.
- A receipt is successful only after voucher readback and attachment readback.
- A failed or ambiguous API workflow stops the batch; archive failures are separate from API failures.

## Preprocessing and analysis workflow

```text
month-local config
  -> usage-confirmation E column + sales/purchase invoice matching
  -> sales_map from 收入成本表.xlsx (sales keys)
  -> purchase_map from 用途确认信息.xlsx / 发票 / E,H,J,K,L (purchase keys)
  -> OCR only on sales / filtered purchase invoice codes
  -> folder party rule and template candidate filtering
  -> DeepSeek template selection and final filling
  -> deterministic review package + preupload_review.json
  -> only after explicit review confirmation: dry-run / confirmed upload
```

For purchase processing this is constrained by:

```text
用途确认信息.xlsx E列 invoice numbers
  intersected with
sales/purchase folder PDFs that share the same invoice code
```

The configured legal entity remains authoritative for OCR direction and party inference.

## Authentication

`login_http.ps1` performs standard manager login, exact company selection, account-book SSO redirect,
token exchange and live company verification (no browser automation dependency).

## Core entry points

- `run_pipeline.py`: unified month preprocessing, OCR, template analysis, receipt preparation, and mode dispatch.
- `scripts/maintenance/scan_xlsx_pdf_map.py`: month-local usage-confirmation matching.
- `batch_receipts.py`: validation and serial voucher/PDF workflow; never bypass review confirmation.
- `src/kdzwy_receipt_uploader/matching.py`: invoice normalization and directory matching.
- `src/kdzwy_receipt_uploader/receipts_ocr.py`: OCR + template candidate reduction + DeepSeek completion.
- `templates/<company-key>/final_template_sample.json`: company-specific DeepSeek final filling contract; directory keys are lowercase ASCII.
- `src/kdzwy_receipt_uploader/preload_items.py`: first-stage source-column scan and item preloading.
- `src/kdzwy_receipt_uploader/preupload_review.py`: review package and confirmation gate.
- `src/kdzwy_receipt_uploader/workflow.py`: live validation, save, readback, attachment link and archive flow.
