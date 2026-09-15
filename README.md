# Persian Smart Accounting (داشبورد هوشمند حسابرسی و حسابداری)

An AI-assisted toolkit for auditing and analyzing the financial documents of
Iranian public/governmental organizations — built and validated against the
real budget forms, financial statements, and audit reports of **Khorasan
Science & Technology Park** (پارک علم و فناوری خراسان).

The project turns a folder of messy, inconsistently-formatted Excel/PDF
government financial documents into structured data, runs a full **financial
audit checklist** against that data, and produces a plain-language,
committee-ready Word report — optionally enriched with an AI-generated
summary of an existing audit report.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Usage](#usage)
7. [Pipeline](#pipeline)
8. [Project Components](#project-components)
9. [Data](#data)
10. [Notebooks](#notebooks)
11. [Testing](#testing)
12. [Troubleshooting](#troubleshooting)
13. [Development](#development)
14. [License](#license)

---

## Overview

**Persian Smart Accounting** automates two related but independent workflows,
both exposed through a **FastAPI + server-rendered HTML/Tailwind** web
dashboard (`api/` + `web/`). The UI was originally built with Streamlit; that
version is preserved for reference under [`legacy_streamlit/`](#project-structure)
but is no longer the primary interface — see [Architecture](#architecture).

1. **Financial audit checklist** — Upload the budget/financial-statement
   Excel (or PDF/legacy `.xls`) files of an organization for a given fiscal
   year. The system:
   - Extracts and structures every relevant sheet (budget forms, revised
     budget, financial statements, trial balance, credit approvals, budget
     law).
   - Runs a full audit checklist (defined in
     `extraction_script/data/Checklist_Question_extracted_handler.json`)
     against the extracted data, evaluating each question automatically where
     possible.
   - Flags every non-conforming ("FALSE") item, and optionally generates an
     AI-written **committee report** (`.docx`) summarizing them — enriched
     with the text of an existing audit report if one is uploaded.
2. **Audit report summarization** — Upload an existing audit report
   (PDF/DOC/DOCX). The system extracts its text (including OCR fallback for
   scanned PDFs) and uses an LLM to produce a plain-language, meeting-ready
   Word summary aimed at board/committee members without an accounting
   background.

**Intended users:** internal auditors, financial committees, and board
members of public-sector organizations who need to quickly verify budget
compliance and review audit findings without manually cross-referencing dozens
of spreadsheets.

## Architecture

The dashboard is a **FastAPI** application (`api/`) with a server-rendered
**Jinja2 + Tailwind** frontend (`web/`) — no separate JS build step, no SPA
framework. Two independent "workspaces" share the same UI shell/components
but have separate processing pipelines, each running in a background thread
so a page can poll job status (`GET /api/<workspace>/jobs/{id}`) without
blocking:

```mermaid
flowchart TD
    UI[web/ - Jinja2 + Tailwind pages] --> WS1[Workspace 1: Checklist]
    UI --> WS2[Workspace 2: Audit report summary]

    WS1 --> API1[api/routers/checklist.py + services/checklist_service.py]
    API1 --> P1[pipeline.py: run_full_pipeline]
    P1 --> EX[extraction_script: budget + financial statement extraction]
    EX --> CL[extraction_script/scripts/checklist: run_audit_pipeline]
    CL --> RPT[audit_report_generator: committee .docx report]

    WS2 --> API2[api/routers/summary.py + services/summary_service.py]
    API2 --> P2[audit_pipeline.py: run_audit_summary]
    P2 --> FI[audit_summarizer: text extraction]
    FI --> LLM[audit_summarizer: LLM summary]
    LLM --> DOC[audit_summarizer: .docx summary]
```

Both workspaces are deliberately kept **thin at the API layer**: routers only
parse the HTTP request and call a service function; services validate/save
uploads and hand off to the unchanged `pipeline.py`/`audit_pipeline.py`
background-job functions; a single in-memory `JobManager`
(`api/jobs/job_manager.py`) tracks job dicts for both workspaces without any
assumption about which workspace a job belongs to.

### Project structure

```
persian-smart-accounting/
├── api/                          # FastAPI application (uvicorn api.main:app --reload)
│   ├── main.py                   # App factory, router registration, global error handler
│   ├── config.py                 # Settings (pydantic-settings), reads the same env vars as before
│   ├── templating.py             # Shared Jinja2Templates instance + template globals (icon())
│   ├── jobs/job_manager.py       # In-memory job registry + lifecycle logging, shared by both workspaces
│   ├── routers/                  # Thin HTTP endpoints: pages.py (HTML), checklist.py, summary.py (JSON)
│   ├── services/                 # Upload validation/saving + job orchestration per workspace
│   ├── schemas/                  # Pydantic response models (common.py holds the shared JobStatus type)
│   └── utils/                    # icons.py (SVG library), uploads.py (adapter), jobs.py (404 helper)
│
├── web/                          # Server-rendered frontend assets
│   ├── templates/                # Jinja2 templates: base.html shell + one template per workspace page
│   │   └── components/           # Reusable partials: dropzone, stepper, progress bar, toast
│   └── static/                   # css/app.css (fonts + small custom styles), js/*.js (per-workspace logic), fonts/
│
├── tests/                        # API-layer tests (FastAPI TestClient), see "Testing"
├── conftest.py                   # Ensures the project root is importable from tests/
│
├── legacy_streamlit/             # Original Streamlit UI (app.py/icons.py/styles.py), kept for reference only
├── pipeline.py                   # Checklist workspace: orchestrates extraction + audit checklist + report
├── audit_pipeline.py             # Audit-summary workspace: thin wrapper around audit_summarizer
├── requirements.txt              # All Python dependencies for the whole project
├── LICENSE                       # MIT License
├── main.ipynb                    # Original prototype notebook the checklist pipeline was extracted from
├── test.ipynb                    # Scratch notebook (filtering FALSE checklist items from a JSON export)
│
├── extraction_script/            # Excel/PDF/DOC data extraction pipelines
│   ├── data/                     # Sample/reference input files and checklist definitions (see "Data")
│   └── scripts/
│       ├── xlsx/
│       │   ├── budget/           # Budget form extraction (parameter-driven + fuzzy auto-detection)
│       │   ├── financial_statements/  # Financial statement extraction (fully auto-detected layout)
│       │   └── taraz/            # Trial balance (تراز آزمایشی) loading helpers
│       ├── checklist/            # Audit checklist evaluation engine
│       └── document_conversion/  # PDF/DOC -> text/xlsx conversion utilities (incl. OCR)
│
├── audit_report_generator/       # LLM-powered "committee non-conformity report" package
├── audit_summarizer/             # Standalone LLM-powered "audit report summary" package (own CLI + tests)
├── db_management/                # Batch/offline PostgreSQL population scripts (independent of the dashboard)
└── docs/
    └── PROGRESS_REPORT.md        # Living development log (weekly progress, architecture history, backlog)
```

**Extensibility notes (read before adding a third workspace):**

- File-slot definitions have a single source of truth in `pipeline.py`
  (`FILE_SLOTS`), consumed by `api/routers/pages.py` (to render the form) and
  `api/services/checklist_service.py` (to validate uploads) — nowhere else.
  The one exception is the audit-summary workspace's single upload slot,
  which is a small dict hardcoded in `api/routers/pages.py::audit_summary_page`
  because `audit_pipeline.py` doesn't define a `FILE_SLOTS`-equivalent
  structure (that file is out of scope for the API/UI migration). This is
  documented in a comment at that call site.
- `JobStatus` (`pending`/`running`/`done`/`error`) has a single definition in
  `api/schemas/common.py`, imported by both `api/schemas/checklist.py` and
  `api/schemas/summary.py`.
- Adding a **third workspace** today would still require touching several
  files, since each workspace currently needs: a router
  (`api/routers/<name>.py`, registered in `api/main.py`), a service
  (`api/services/<name>_service.py`), Pydantic schemas
  (`api/schemas/<name>.py`), a page template (`web/templates/<name>.html`)
  and its own JS controller (`web/static/js/<name>.js`), plus a link on
  `web/templates/index.html` and the nav in `web/templates/base.html`. All of
  these follow the same copy-paste-and-adapt pattern between the checklist
  and audit-summary workspaces today; if a third workspace is added, it's
  worth extracting a small shared "workspace registry" (e.g. a list of
  `{slug, title, icon, router}` used to generate the landing page cards and
  nav links automatically) rather than continuing to hand-edit `index.html`
  and `base.html` for each new workspace. This refactor was **not** done in
  this phase since only two workspaces exist and it would add abstraction
  without a second concrete use case to validate it against.
- The reusable UI building blocks (`web/templates/components/*.html` +
  their companion `window.psaSetStep` / `psaSetProgress` / `psaShowToast`
  globals) are already workspace-agnostic and require no changes to support
  a third workspace.

**Design notes:**

- `extraction_script` has no `__init__.py` files; it (and its sub-packages)
  are used as **implicit namespace packages**. Every entry point that imports
  from it (`legacy_streamlit/app.py`, `pipeline.py`,
  `db_management/budget_management/insert.py`) inserts the project root onto
  `sys.path` first — this is intentional and documented in each file's header
  comment. `api/main.py` relies on this too: it can import `pipeline.py`
  because it's run from the project root (`uvicorn api.main:app`). Do not add
  `__init__.py` files to this tree without re-checking all the `sys.path`
  insertion points.
- `audit_report_generator` and `audit_summarizer` are two **independent**
  LLM-integration packages (different providers, different prompt strategies,
  different output structure) used for two different report types. They are
  not merged into one because they serve genuinely different purposes and
  `audit_summarizer` is designed to be usable completely standalone (own
  `requirements.txt`, `README.md`, CLI, and test suite).
- `audit_summarizer/file_ingest.py` and
  `extraction_script/scripts/document_conversion/file_to_text.py` are
  intentionally identical copies of the same text-extraction module — one for
  the standalone `audit_summarizer` package and one for the main dashboard's
  own PDF/DOC ingestion path. This avoids introducing a hard dependency of
  `audit_summarizer` on the rest of the repository. If you fix a bug in one,
  please mirror the fix in the other.

## Requirements

- **Python** 3.10 or newer (the codebase uses `from __future__ import
  annotations` and `X | None` union syntax throughout).
- **PostgreSQL** (only required for `db_management/` batch scripts — not
  required to run the web dashboard itself).
- **System binaries** (required for PDF/DOC processing features):
  - [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) — needed for
    OCR fallback on scanned/image-only PDFs.
  - [Poppler](https://poppler.freedesktop.org/) — needed by `pdf2image` to
    rasterize PDF pages for OCR.
  - [LibreOffice](https://www.libreoffice.org/) — needed to convert legacy
    `.doc`/`.rtf`/`.odt` files to `.docx` before text extraction, and to
    convert final `.docx` summaries to `.pdf`.
- No GPU/CUDA is required; all AI features call a remote LLM API over HTTPS.

## Installation

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. (Optional, only for OCR/legacy-doc support) install system binaries
#    Windows: install Tesseract-OCR and Poppler, then either place them on
#    PATH or point to them via environment variables (see Configuration).
#    Ubuntu/Debian:
sudo apt-get install tesseract-ocr tesseract-ocr-fas poppler-utils libreoffice
#    macOS:
brew install tesseract poppler --cask libreoffice
```

## Configuration

All secrets are read from environment variables (via `python-dotenv`, so a
`.env` file in the project root is also supported — see `.env.sample`).
**Never commit real API keys or database passwords.**

| Variable | Used by | Purpose |
|---|---|---|
| `B_AI_API_KEY` (or legacy `API_KEY_B_AI`) | `audit_summarizer` (audit report summary workspace) | API key for the `api.b.ai` OpenAI-compatible chat-completions endpoint |
| `API_KEY_OPENROUTER` | `pipeline.py` (committee report generation) | Fallback API key for the OpenRouter provider used to generate the committee report |
| `AUDIT_REPORT_LLM_API_KEY` | `pipeline.py` | Overrides `API_KEY_OPENROUTER` for the committee report LLM call |
| `AUDIT_REPORT_LLM_BASE_URL` | `pipeline.py` | LLM base URL for the committee report (default: `https://openrouter.ai/api/v1`) |
| `AUDIT_REPORT_LLM_MODEL` | `pipeline.py` | Model name for the committee report (default: `nvidia/nemotron-3-ultra-550b-a55b:free`) |
| `AUDIT_LLM_API_KEY` / `OPENAI_API_KEY` | `audit_report_generator` (as a standalone package/CLI) | API key resolution order used by `LLMConfig.resolve_api_key()` |
| `TESSERACT_CMD` | `audit_summarizer`, `extraction_script` document conversion | Explicit path to the `tesseract` binary. If unset, the code falls back to the default Windows install path if present, otherwise relies on `tesseract` being on `PATH` (the default on Linux/macOS) |

Additional configuration files:

- `api/config.py` — API-layer-only settings (`PSA_MAX_UPLOAD_MB`,
  `PSA_JOB_TTL_SECONDS`, `PSA_LOG_LEVEL`), also loaded from `.env`. It does
  **not** change how `pipeline.py`/`audit_report_generator` read their own
  LLM environment variables — those are untouched and still read directly
  via `os.getenv`/`python-dotenv`.
- `.streamlit/config.toml` — theme/server settings for the legacy Streamlit
  UI (`legacy_streamlit/app.py`), not used by the FastAPI app.
- `extraction_script/scripts/xlsx/budget/config.py` — manual layout
  parameters (`FORMS_PARAM`), sheet-name-to-form mapping
  (`SHEET_TO_CONFIG_MAP`), and fuzzy content-keyword mapping
  (`CONTENT_KEYWORD_MAP`) used to recognize each budget form.
- `db_management/budget_management/{setup,tables,insert}.py` — currently
  contain **hardcoded local development database credentials**
  (`smart_acounting_db` / `postgres` / a placeholder password). These scripts
  are standalone, offline batch tools independent of the web dashboard; if
  you intend to use them beyond local development, replace the hardcoded
  credentials with environment variables before doing so.

## Usage

### Run the dashboard

```bash
uvicorn api.main:app --reload
```

Run this from the project root (see the `sys.path` note under
[Project structure](#project-structure)). Then open
[http://127.0.0.1:8000/](http://127.0.0.1:8000/) — the landing page links to
the two workspaces: "بررسی چک‌لیست حسابرسی" (`/checklist`, the financial
audit checklist) and "خلاصه‌سازی گزارش حسابرسی" (`/audit-summary`, audit
report summarization). Interactive API docs are auto-generated by FastAPI at
`/docs`.

The original Streamlit UI is still available for reference/comparison under
[`legacy_streamlit/`](#project-structure) (`streamlit run legacy_streamlit/app.py`),
but it is no longer maintained as the primary interface.

### Run the audit report summarizer standalone (CLI)

```bash
cd audit_summarizer
python main.py --input report.pdf --output-format docx
```

See `audit_summarizer/README.md` for the full CLI reference.

### Run the committee report generator standalone (CLI)

```bash
python -m audit_report_generator.cli \
    --checklist-json-file sample_data/checklist_false_items.json \
    --doc-text-file sample_data/audit_report.txt \
    --output output/گزارش_کمیسیون.docx
```

### Populate the PostgreSQL database from budget files (offline batch tool)

```bash
python db_management/budget_management/setup.py   # create the database
python db_management/budget_management/tables.py  # create the schema
python db_management/budget_management/insert.py  # extract + insert budget data
```

This is a separate, independent workflow from the web dashboard — it
persists extracted budget data into PostgreSQL for downstream querying, but
is not (yet) read by the dashboard itself.

## Pipeline

### Financial audit checklist workspace

```
Uploaded files (budget/revised budget/financial statements/trial balance/…)
  ↓
xls/pdf → xlsx normalization (pipeline.save_uploaded_file)
  ↓
Specialized extraction per document type
  (extraction_script.scripts.xlsx.budget.budget_process — budget forms
   extraction_script.scripts.xlsx.financial_statements.process — financial statements
   pandas.read_excel + fuzzy content matching — trial balance)
  ↓
Sheet merge into one unified in-memory dataset (IMPORTED_DF)
  ↓
Year/column relabeling (extraction_script.scripts.checklist.year_relabeler)
  ↓
Audit checklist evaluation, question by question
  (extraction_script.scripts.checklist.checklist_process.run_audit_pipeline)
  ↓
Non-conforming ("FALSE") items collected
  ↓
Committee report generation (audit_report_generator, LLM-powered, optional)
  ↓
Output: on-screen results + downloadable .docx committee report
```

### Audit report summarization workspace

```
Uploaded audit report (PDF/DOC/DOCX)
  ↓
Text extraction (PyMuPDF → pdfplumber → pypdf fallback chain, + OCR fallback for scans)
  ↓
Prompt construction (audit_summarizer.llm_client.build_summary_prompt)
  ↓
LLM call (api.b.ai OpenAI-compatible endpoint)
  ↓
Word document rendering (audit_summarizer.doc_writer, RTL/Persian formatting)
  ↓
Output: downloadable .docx meeting-ready summary
```

## Project Components

| Component | Responsibility |
|---|---|
| `api/` | FastAPI app: routers (HTTP), services (upload validation + job orchestration), schemas (Pydantic response models), the shared in-memory `JobManager` |
| `web/` | Jinja2 templates + Tailwind classes + small per-workspace JS controllers (upload → poll → render results); no build step |
| `pipeline.py` | Orchestrates the checklist workspace end-to-end; background-thread job management (unchanged business logic, called from `api/services/checklist_service.py`) |
| `audit_pipeline.py` | Orchestrates the audit-summary workspace; background-thread job management (unchanged business logic, called from `api/services/summary_service.py`) |
| `legacy_streamlit/` | Original Streamlit UI (`app.py`, `icons.py`, `styles.py`), preserved for reference; not the primary interface anymore |
| `extraction_script/scripts/xlsx/budget/` | Budget form extraction: parameter-driven (`config.py`) + fuzzy sheet/content matching |
| `extraction_script/scripts/xlsx/financial_statements/` | Financial statement extraction with fully automatic layout detection |
| `extraction_script/scripts/xlsx/taraz/` | Trial balance loading helper |
| `extraction_script/scripts/checklist/` | Audit checklist evaluation engine: condition parsing, formula evaluation (`math_func_to_latex_code.py`), fuzzy metadata search, year/column relabeling |
| `extraction_script/scripts/document_conversion/` | PDF → xlsx conversion for tabular Persian PDFs, and generic PDF/DOC → text extraction (with OCR fallback) |
| `audit_report_generator/` | LLM-powered generator of the "committee non-conformity report" (`.docx`), built on the `openai` client library |
| `audit_summarizer/` | Standalone LLM-powered audit-report summarizer package, with its own CLI, tests, and samples |
| `db_management/budget_management/` | Batch scripts to create the PostgreSQL schema and bulk-insert extracted budget data |

## Data

`extraction_script/data/` contains the real reference input files and
checklist definitions this project was built and validated against:

- **Budget files**: `1- ابلاغ بودجه مصوب سال 98.xlsx`, `ابلاغ.xlsx`,
  `اصلاحیه بودجه تفصیلی 98 - 991212 (1).xlsx` and its original `.pdf`,
  `افزایش سقف درآمد اختصاصی 98.{xlsx,pdf}`,
  `تاييديه اعتبارات هزينه اي...98.{xlsx,pdf}`.
- **Financial statements**: `صورتهاي مالي 1398.{xlsx,pdf}`.
- **Trial balance**: `تراز 98.{xls,xlsx}`.
- **Audit report**: `گزارش  اول پارک خراسان سال 1398.doc`,
  `گزارش_حسابرسی(1).json`.
- **Checklist definitions**:
  `Checklist_Question_extracted_examples.json`,
  `Checklist_Question_extracted_handler.json` (the one actually loaded by
  `pipeline.py` at runtime), `Checklist_Question_extracted_handler_def.json`.
- **Scratch outputs**: `check_budget.xlsx`, `check_output.xlsx`, `Report.pdf`.

This directory is treated as read-only reference/sample data by the
application; uploaded files from the dashboard are processed in a separate
temporary working directory (created by `pipeline.make_temp_workdir()` /
`audit_pipeline.make_audit_temp_workdir()`) and cleaned up after each run.

## Testing

API-layer tests live in `tests/` and use FastAPI's `TestClient` (`httpx`
under the hood). They cover job creation, status polling, results/download,
and error handling (missing upload, invalid file type, unknown job id) for
**both** workspaces. They never run the real extraction/checklist/LLM
pipelines: `pipeline.start_checklist_job` and
`audit_pipeline.start_audit_summary_job` — the only functions that spawn the
heavy background thread — are monkeypatched with a synchronous stand-in, so
the tests run in well under a second and need no Tesseract/LibreOffice/LLM
API access.

```bash
# from the project root
pytest tests -q
```

`audit_summarizer` also has its own independent test suite (see
[Development](#development)):

```bash
cd audit_summarizer
pytest -q
```

There is currently no automated test suite for the extraction/checklist
pipeline logic itself (`extraction_script/`, `pipeline.py`,
`audit_pipeline.py`) — see `docs/PROGRESS_REPORT.md` for known backlog items,
including this one. This was intentionally out of scope for the API/UI
migration phases.

## Troubleshooting

- **`ModuleNotFoundError` when running scripts directly** — most modules rely
  on `sys.path` insertion performed at the top of the entry-point file (e.g.
  `pipeline.py`, `legacy_streamlit/app.py`,
  `db_management/budget_management/insert.py`). Run scripts from the project
  root, or via the documented entry points, rather than executing a deeply
  nested file directly. The FastAPI app is affected too: always run
  `uvicorn api.main:app` from the project root.
- **`API_KEY ... در فایل env پیدا نشد` errors** — set the corresponding
  environment variable (see [Configuration](#configuration)) or add it to a
  `.env` file in the project root.
- **OCR fails / "Tesseract is not installed" errors** — install Tesseract OCR
  and, on Windows, either let the code auto-detect the default install path
  (`C:/Program Files/Tesseract-OCR/tesseract.exe`) or set `TESSERACT_CMD`.
- **`.doc` file conversion fails** — LibreOffice must be installed and
  available on `PATH` (used via `subprocess` to convert `.doc`/`.rtf`/`.odt`
  to `.docx`).
- **PDF text comes out mirrored/reversed (Persian/Arabic)** — this is a known
  limitation of coordinate-based PDF text extractors (`pdfplumber`, `pypdf`)
  for right-to-left scripts. The code prefers PyMuPDF (correct logical
  reading order) and includes a heuristic safety net
  (`_fix_reversed_rtl_lines`) for the fallback paths; if a specific PDF still
  comes out reversed, it likely needs `pymupdf` installed (it is optional at
  import time).
- **`psycopg2` connection errors in `db_management/`** — these scripts assume
  a local PostgreSQL instance with the hardcoded credentials in
  `budget_management/{setup,tables,insert}.py`; update those values (or
  migrate them to environment variables) to match your database.

## Development

- **Add a new budget form type**: extend `FORMS_PARAM` and
  `SHEET_TO_CONFIG_MAP` in `extraction_script/scripts/xlsx/budget/config.py`.
- **Add a new checklist question**: add it to
  `extraction_script/data/Checklist_Question_extracted_handler.json`,
  following the existing `evaluation_logic` schema documented in
  `extraction_script/scripts/checklist/checklist_process.md`.
- **Add a new file type/upload slot** to the checklist workspace: extend
  `FILE_SLOTS` in `pipeline.py` and wire it into
  `pipeline.build_imported_sheets` — no change needed in `api/` or `web/`,
  since `api/routers/pages.py` and `api/services/checklist_service.py` both
  read `FILE_SLOTS` dynamically (see the extensibility notes under
  [Project structure](#project-structure)).
- **Modify LLM behavior**: `audit_summarizer/llm_client.py` (audit summary
  prompt) and `audit_report_generator/prompt_builder.py` (committee report
  prompt) are the two prompt-construction entry points.
- **Run tests**: see [Testing](#testing) for the API-layer suite
  (`pytest tests -q`) and the standalone `audit_summarizer` suite.
- **Extend the pipeline**: both `pipeline.run_full_pipeline` and
  `audit_pipeline.run_audit_summary` are plain functions designed to be called
  directly (outside of the web app) for scripting/testing purposes; the
  `job` dict parameter is optional and only needed for live progress
  reporting in the dashboard.
- **Add a third workspace**: see the extensibility notes under
  [Project structure](#project-structure) for the current list of files
  you'd need to touch.

## License

MIT License — see [`LICENSE`](LICENSE) for the full text.
