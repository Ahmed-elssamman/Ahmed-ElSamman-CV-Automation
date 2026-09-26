# Original CV ingestion and reconciliation

`workai ingest first.pdf second.pdf` accepts one or two original PDFs. Archives,
source SHA-256 hashes, page text and annotation URLs live under `data/source-cv/`.
Original input files are never modified. Duplicate bytes reuse the existing
archive, even under another filename. At most two distinct source PDFs may be
archived; a third fails before any new archive is written. Prior legacy duplicate
archives remain untouched.

The conservative parser supports case-insensitive section headings and aliases
such as Professional Summary, Work Experience, Employment History and Technical
Skills. Roles may use a title/location row plus company/date row, or separate
title, company and full-month date rows. Skills may be comma, semicolon, bullet
or newline separated. Certificates support the original year/backslash format
and `Certificate | Issuer | Year`. Unrecognized structures fail for review rather
than guessing. This is not a universal CV parser; extraction artifacts remain
available when normalization fails.

## Two-source reconciliation

`reconcile_profiles(profiles, canonical=None)` combines evidence for matching
facts and unions skills, bullets and distinct records. Every added fact retains
source/page/quote provenance. On first ingestion, conflicting scalar values
become YAML `null` (UNKNOWN); conflicting historical records are withheld as
whole records so partial dates or titles cannot create misleading claims.
Complete alternatives and their provenance remain in `conflicts`, with
`NEEDS_USER_INPUT`, and in `data/master-cv/conflicts.json`.

On later ingestion, the existing canonical value is retained when a new original
CV disagrees, and a conflict records both assertions. Explicit user facts and
canonical history take priority over original CV claims. Source normalization
cannot change legal facts, availability, salary or other user preferences.
Compatible new facts and missing contacts can still be added. A repeat of the
same source is idempotent. Explicit user clarifications may resolve conflicts;
the conflict remains as history with its resolution source.

Record matching is intentionally conservative: company/title for employment,
institution for education, and name for projects, certificates and languages.
Two degrees from the same institution with differing descriptions therefore
require review. Different titles can describe separate jobs and are retained.
This cannot detect every semantic contradiction in free prose. It never uses a
job description to supply candidate facts.

## Optional local OCR

`workai ingest --ocr --ocr-language eng scanned.pdf`, or the Python API
`ingest_pdfs(root, paths, ocr=True, ocr_language="eng")`, enables
local OCR with Poppler `pdftoppm` and Tesseract. Without OCR, scanned pages raise
`OCR_REQUIRED`; missing OCR tools raise `OCR_DEPENDENCY_MISSING`. Neither tool is
downloaded automatically. Install Tesseract language data for the requested
language. Rendering and OCR have bounded subprocess timeouts and use private,
removed temporary files. Text-only extraction uses Poppler `pdftotext`, with a
pypdf fallback.

OCR output is stored immutably as `*.extracted.json` with `ocr_unverified` and
page numbers. It cannot enter the canonical profile or a generated CV. Review
all pages against the original PDF, correct transcription mistakes, then call:

```python
from workai.profile import review_ocr, ingest_pdfs

review_ocr(
    root, source_id,
    reviewed_pages=[{"page": 1, "text": complete_reviewed_page_text}],
    reviewer="user:reviewer-identifier",
    expected_extraction_sha256=manifest_source["extraction_sha256"],
    note="Compared every source page and corrected transcription mistakes.",
)
profile = ingest_pdfs(root, [original_pdf])
```

The CLI equivalent reads the complete reviewed page list from a JSON file:

```bash
workai review-ocr 'sha256:<original-source-hash>' \
  --pages-json reviewed-pages.json --reviewer 'user:reviewer-identifier' \
  --extraction-sha256 '<extraction-artifact-hash>' \
  --note 'Compared every source page and corrected transcription mistakes.'
workai ingest scanned.pdf
```

The review must provide every page exactly once in source order. It is bound to
the extraction hash, preserves raw OCR, records reviewer/note and writes a
separate immutable reviewed artifact. A different review cannot overwrite the
first. This API records an actual completed review; merely supplying an identity
is not evidence that review occurred. Review verifies transcription, not the
truth of employment assertions. `load_source_extraction(root, source)` checks
archive/extraction/review hashes before supplying reviewed OCR to CV validation.

## Canonical updates and recovery

All canonical writes use atomic file replacement and an advisory writer lock.
Every canonical version has an immutable content-addressed snapshot under
`data/master-cv/revisions/<sha256>/`. Ingestion and explicit clarification use
optimistic SHA-256 checks: a concurrent edit produces `PROFILE_CONCURRENT_EDIT`
instead of overwriting intervening facts. Callers of `apply_user_clarification`
may supply `expected_sha256` to reject a stale user edit before storing approval.
The approval retains exact source text and the previous canonical bytes.

Section YAML files are projections and carry `canonical_sha256`. Their individual
replacements are atomic, but the entire set of projection files is not a single
filesystem transaction: interruption may leave a stale projection. Runtime
consumers read only `profile.yaml`; compare hashes before reading projections.
Regenerating projections with `_persist_profile(root, load_profile(root),
expected_sha256=...)` repairs interrupted exports without changing canonical
facts. Immutable source/review files must never be edited to repair a mismatch.

Deterministic tests exercise alternate layouts, two-source merging/conflicts,
canonical precedence, archive limits, duplicates, OCR review/hash gates,
missing OCR dependencies and stale-edit rejection. Mock OCR tests verify the
review gate, not Tesseract recognition quality. Live OCR requires local tools;
this environment also passed a real image-only PDF check using Poppler and
Tesseract 5.5.3. The executable and English data were extracted from Fedora RPMs
under ignored `.tools/ocr`, with no system package changes. To use that local
installation from the CLI:

```bash
export PATH="$PWD/.tools/ocr/usr/bin:$PATH"
export TESSDATA_PREFIX="$PWD/.tools/ocr/usr/share/tesseract/tessdata"
workai ingest --ocr scanned.pdf
```

The real OCR integration test discovers that optional local installation; on a
machine without Poppler/Tesseract it reports a skip. Recognition was checked on
a generated fixture, not on a live candidate CV.
