# Event Detail Tabs — Phased Plan

JobNimbus-style tabbed layout on the event detail page, so files and invoices live inside each lead instead of in a parallel system.

## Goal

Replace the long scroll on `frontend/src/pages/EventDetail.jsx` with a left-rail tab menu. The current sections become an `Overview` tab. Add `Documents` and `Invoices` tabs in v1. Reserve room for `Photos`, `Activity`, `Notes`, `Tasks` in a later release.

## Working environment

All build and verification work happens on the VPS. There is no local dev server. Smoke tests that say "visit `/events/<id>`" mean visit the deployed admin host (`admin.shopbellasxv.com`) after the VPS rebuild and service restart, not a localhost URL.

## Decisions locked

- Files stored locally on the VPS in v1. Default root: `/var/lib/bellas-xv/uploads`. This avoids adding another paid system while volume is low.
- Storage service should keep a narrow interface so we can swap to Backblaze B2 or another object store later without rewriting the event document APIs.
- Documents and invoices share one table (`event_documents`) with a `kind` discriminator. Invoice-only fields live as nullable columns on the same row.
- Invoices in v1: uploaded file plus `amount_cents` and `status`. No invoice generation, no payment links, no line items.
- Any logged-in user can upload, list, and download. Delete gated in Phase 5.
- Upload limits: 25 MB max. Allowed types: `pdf`, `jpg`, `jpeg`, `png`, `heic`, `docx`.
- Files attach to `event_id` only. No attaching to bookings or contacts in v1.
- Routing: `/events/:id/:tab` with `:tab` in `{overview, documents, invoices}`. Bare `/events/:id` redirects to `/events/:id/overview`.

## Tracking

- [x] Phase 0: Confirm baseline
- [x] Phase 1: Tab layout shell + Overview tab
- [x] Phase 2: Document storage + database + APIs
- [x] Phase 3: Documents tab
- [x] Phase 4: Invoices tab
- [x] Phase 5: Polish
- [x] Phase 6: Tests and verification

---

## Phase 0: Confirm baseline

Purpose: catch divergence between this plan and the real code before we touch anything.

Tasks:

- [ ] Re-read `frontend/src/pages/EventDetail.jsx` for the current section layout (Event, Primary contact, Participants, Booking, Status history).
- [ ] Re-read `api/routers/events.py` for `EventDetailResponse` shape. Confirm we are not duplicating fields the tab feature does not need.
- [ ] Re-read `frontend/src/App.jsx` (or wherever React Router is configured) to confirm how `/events/:id` is wired today.
- [ ] Confirm the production upload directory location, filesystem permissions, and backup expectation. Default: `/var/lib/bellas-xv/uploads`, owned by the API service user.
- [ ] Confirm there is no existing `event_documents` table or upload code that this plan would collide with.

Deliverable: short follow-up notes appended to this doc if reality differs.

---

## Phase 1: Tab layout shell + Overview tab

Purpose: ship the tabbed chrome with zero functional regression. Existing detail UI moves under an `Overview` tab. Other tabs render placeholders.

Tasks:

- [ ] Create `frontend/src/pages/event/EventDetailLayout.jsx`. Left rail with the three tabs, right pane renders the active tab via React Router `Outlet`.
- [ ] Update routes so `/events/:id` redirects to `/events/:id/overview`. Sub-routes: `/events/:id/overview`, `/events/:id/documents`, `/events/:id/invoices`.
- [ ] Move the current body of `EventDetail.jsx` into `frontend/src/pages/event/tabs/Overview.jsx`. The header (back link, title, status dropdown) stays in the layout, not inside the tab.
- [ ] Add placeholder tab components `Documents.jsx` and `Invoices.jsx` with a "coming soon" empty state.
- [ ] Match left-rail visual style to the existing admin sidebar (`Pipeline`, `Calendar`, etc.).

Smoke test (manual):

- Visit `/events/<id>` for a known event. Redirects to `/events/<id>/overview` and shows everything that used to be on the page.
- Click each tab. URL updates. Browser back/forward navigates between tabs.
- Refresh on `/events/<id>/documents`. Documents tab is selected on load, no flash to Overview.
- Status dropdown still mutates correctly from the layout header.

Deliverable: visual change only. No backend, no schema, no new data.

---

## Phase 2: Document storage + database + APIs

Purpose: a place to put files and the endpoints to manage them. No UI yet, verify with curl.

Tasks:

- [ ] New env vars in `config/settings.py`: `DOCUMENT_STORAGE_BACKEND=local`, `DOCUMENT_STORAGE_ROOT=/var/lib/bellas-xv/uploads`, `DOCUMENT_UPLOAD_MAX_MB=25`. Add to `.env.example`.
- [ ] New service `services/document_storage.py`. Thin wrapper over local filesystem storage. Methods: `put_object(key, fileobj, content_type)`, `open_object(key)`, `delete_object(key)`, `resolve_path(key)`. Storage keys follow `events/{event_id}/{document_id}/{slugified_filename}`.
- [ ] Ensure `resolve_path` prevents path traversal and never returns a path outside `DOCUMENT_STORAGE_ROOT`.
- [ ] New migration `017_create_event_documents.py`:
  - Columns: `id`, `event_id` FK to `events.id` ON DELETE CASCADE, `uploaded_by_user_id` FK users SET NULL, `kind` (`document` or `invoice`), `filename`, `content_type`, `byte_size`, `storage_key`, `label`, `deleted_at`, `created_at`, `updated_at`.
  - Invoice-only nullable columns on the same row: `invoice_amount_cents`, `invoice_status` (`draft`, `sent`, `paid`, `void`), `invoice_issued_at`, `invoice_paid_at`.
  - CHECK constraint: invoice columns must be NULL when `kind != 'invoice'`.
  - Indexes: `(event_id, kind, deleted_at)` and `(event_id, created_at DESC)`.
- [ ] New ORM model `EventDocument` in `database/models.py` matching the migration.
- [ ] New router `api/routers/event_documents.py`:
  - `POST /api/events/{event_id}/documents` (multipart). Form fields: `file`, `kind`, `label?`. Validates type and size, generates a fresh `id`, streams to local storage under the canonical key, inserts the row.
  - `GET /api/events/{event_id}/documents?kind=` lists non-deleted rows.
  - `GET /api/documents/{document_id}/download` streams the stored file back with the original filename as the download name.
  - `PATCH /api/documents/{document_id}` updates `label` and, when `kind=invoice`, the four invoice fields. Rejects invoice fields on non-invoice rows.
  - `DELETE /api/documents/{document_id}` soft-deletes (sets `deleted_at`). The local file stays for now. Hard-delete sweep is a deferred concern.
- [ ] Require the same authenticated user dependency on every document route, including downloads. No unauthenticated or bearerless local-file URLs.
- [ ] Wire router into `api/server.py`.
- [ ] Add real INSERTs to verify the schema before moving on (per repo convention).

Smoke test (`tests/test_event_documents_smoke.py`):

- Upload a small PDF to a freshly created event. Row created, `byte_size` matches, `storage_key` returned.
- List documents for the event. Row appears.
- `GET /api/documents/{id}/download` returns the stored bytes with a download filename.
- Reject a 30 MB upload with a 413.
- Reject an `.exe` upload with a 415.
- Soft-delete the row. List excludes it. Direct GET 404s.
- `PATCH` invoice metadata on a `kind=invoice` row succeeds.
- `PATCH` invoice metadata on a `kind=document` row 422s.
- DB-level CHECK constraint blocks a row with `kind=document` and a non-null `invoice_amount_cents`.

Deliverable: working backend without UI. Curl-verifiable end-to-end.

Validation note, 2026-04-30:

- `venv/bin/python tests/test_event_documents_smoke.py` passed.
- Existing smokes passed: `tests/test_events_smoke.py`, `tests/test_booking_smoke.py`, `tests/test_boutique_experience_smoke.py`.
- DB check confirmed `event_documents` exists, all four planned CHECK constraints are present, both planned indexes exist, and the invoice-only-fields CHECK rejects an invalid real insert.
- No local dev server was run; API browser/curl verification still requires the VPS service restart.

---

## Phase 3: Documents tab

Purpose: real upload/list/download/rename/delete UI for `kind=document`.

Phase 3 note: resolved in the amendments below. The rename/edit UI can clear labels because the API now distinguishes omitted `label` from an explicit `null` or empty string.

Tasks:

- [ ] Add `listDocuments`, `uploadDocument`, `downloadDocument`, `renameDocument`, `deleteDocument` to `frontend/src/services/api.js`.
- [ ] Build `frontend/src/pages/event/tabs/Documents.jsx`:
  - Drag-drop uploader plus file picker. Progress indicator per file.
  - Client-side type and size validation mirroring server limits.
  - List rows: filename, label (inline editable), uploader, size, uploaded date. Actions: download, rename, delete.
  - Empty state copy in the repo voice (no robotic listy "X, and Y" patterns, no em dashes).
- [ ] React Query keys: `['event', id, 'documents', { kind: 'document' }]`. Invalidate on every mutation.

Smoke test (manual):

- Upload a PDF, a JPG, and an HEIC file. All appear with the right type icon.
- Rename a document. Refresh the page. Name persists.
- Click download. Browser downloads the original file with the original filename.
- Delete a document. Disappears from the list. Refresh confirms.
- Try to drop a 30 MB file. Client-side rejection with a clear message, no request fired.
- Upload while offline (DevTools throttle). Clear error state, no half-row in the list.

Deliverable: shop staff can attach a contract to a lead.

Phase 3 amendments, 2026-05-01:

- Label clear: `PATCH /api/documents/{id}` now uses `model_fields_set` so the client can clear `label` by sending `{"label": null}` or `{"label": ""}`. Omitting the key still leaves the value alone. Smoke test extended.
- In-tab preview added (was not in the original plan). Clicking the filename opens the file in a new tab with browser-native PDF/image preview and the browser's own download/print controls. Backend: `GET /api/documents/{id}/download` accepts `?disposition=inline|attachment`, default `attachment`. Frontend: new `viewDocument(id)` opens a tab synchronously (popup-blocker safe) and swaps in a blob URL once the auth-headered fetch resolves. Download icon still forces a save.
- Smoke additions: default disposition is `attachment`; `?disposition=inline` flips the header; bogus values 422.

---

## Phase 4: Invoices tab

Purpose: same pattern as Documents, with structured invoice fields.

Tasks:

- [ ] Build `frontend/src/pages/event/tabs/Invoices.jsx`:
  - Same uploader and list pattern as Documents, scoped to `kind=invoice`.
  - Each row: filename, amount, status pill, issued date, paid date, actions.
  - Inline edit for `invoice_amount_cents` (currency input) and `invoice_status` (dropdown).
  - Header summary: total billed, total paid, total outstanding.
- [ ] Server side: when `PATCH` moves status to `paid`, auto-stamp `invoice_paid_at` if not provided. When status moves off `paid`, clear it. Same for `sent` and `invoice_issued_at`.

Smoke test (manual):

- Upload an invoice PDF. Set amount to $1,250 and status to `sent`. Reload. Persists. `invoice_issued_at` populated.
- Mark as `paid`. `invoice_paid_at` populates. Header `total paid` increments and `outstanding` drops.
- Move back to `sent`. `invoice_paid_at` clears.
- Delete an invoice. Header totals update.
- Documents tab does not show invoices. Invoices tab does not show documents.

Deliverable: shop staff can track per-lead billing without leaving the lead page.

Validation note, 2026-05-01:

- `cd frontend && npm run lint` passed.
- `cd frontend && npm run build` passed. Vite still reports the existing large chunk warning for the main JS bundle.
- `venv/bin/python tests/test_event_documents_smoke.py` passed, including inline download disposition, bogus disposition rejection, label clearing, invoice status auto-stamps, kind filtering, soft delete, and oversize rollback.
- Code inspection confirmed `Invoices.jsx` is scoped to `kind=invoice`, computes billed/paid/outstanding client-side, excludes void invoices from billed totals, edits amount/status inline, and uses the shared authenticated blob preview/download helpers.
- No local dev server was run; manual browser smoke still happens on `admin.shopbellasxv.com` after VPS rebuild/restart.

---

## Phase 5: Polish

Purpose: edges that are not blockers but matter for daily use.

Tasks:

- [ ] Tab badge counts: `Documents (3)`, `Invoices (2)`. Single fetch on layout load via `GET /api/events/{id}/document-counts`.
- [ ] Per-tab empty-state copy with an inline CTA (`Drop files here or click to upload`).
- [ ] Delete confirmation dialog for both Documents and Invoices.
- [ ] Permissions: only the uploader or a user with role `admin` can delete a row. Others see the action disabled with a tooltip explaining why.
- [ ] Outstanding-invoice badge on Pipeline kanban cards. Small dot or pill, only when any linked invoice is `sent` and unpaid.
- [ ] Structured log line on every upload, patch, and delete. Includes `user_id`, `event_id`, `document_id`, `kind`, `byte_size`.
- [ ] Backup story for `/var/lib/bellas-xv/uploads`: either extend the existing daily backup job to include the upload directory or explicitly document that local uploads are not durable until object storage migration.
- [ ] Disk-space guard for the upload filesystem. Minimum v1: log a warning when usage is over 80% and fail uploads cleanly before the disk is full.

Smoke test:

- Tab counts match list lengths after upload and after delete.
- A non-admin staff user cannot delete an invoice uploaded by someone else. Can still delete their own.
- A Pipeline card with one unpaid `sent` invoice shows the outstanding badge. Card with no invoices does not.
- Backup command includes the upload directory, or the durability tradeoff is explicitly documented in production notes.
- Disk-space check logs a warning above 80% usage and rejects uploads with a clear server error when free space is too low.

Deliverable: feature is staff-friendly and production-ready.

Validation note, 2026-05-01:

- Phase 5 code paths validated: document counts endpoint, board `has_outstanding_invoice`, delete authorization, structured logging calls, disk-space guard, tab badges, MUI delete dialog, Pipeline outstanding badge, and disabled delete tooltip.
- `venv/bin/python tests/test_event_documents_smoke.py` passed with counts, board flag, paid-invoice clearing, delete authorization, admin override, soft delete, size rollback, and path traversal coverage.
- Regression smokes passed: `tests/test_events_smoke.py`, `tests/test_booking_smoke.py`, and `tests/test_boutique_experience_smoke.py`.
- `cd frontend && npm run lint` passed.
- `cd frontend && npm run build` passed. Vite still reports the existing large chunk warning for the main JS bundle.
- Remaining Phase 5 item: decide the backup/durability story for `/var/lib/bellas-xv/uploads`. Keep the Phase 5 tracker open until that ops decision is made or documented.

Backup decision, 2026-05-01:

- Investigation: no existing daily backup job is wired on the VPS. `~/backups/` does not exist; no user/root crontab entry; no systemd timer; no `pg_dump` script anywhere on the box. `VPS_HARDENING.md` Step 9 (Backblaze + daily backups) is still pending. There was no "existing daily backup job" to extend.
- Decision: take the explicit-tradeoff option from the Phase 5 plan. Local uploads under `/var/lib/bellas-xv/uploads` are **not durable** in v1. A complete VPS loss would lose every uploaded contract, invoice, and inspiration sheet stored to date. This matches the durability profile of Postgres on this box (also not backed up off-server today), so it does not introduce a new class of risk; it widens the existing one.
- Operational expectations until durability lands:
  - Treat anything in `/var/lib/bellas-xv/uploads` as reproducible-from-customer. If a contract is critical, keep an email or paper copy too.
  - Do not migrate the shop off legacy file storage onto this system. Bellas XV is the secondary; legacy is still authoritative for any document the business cannot lose.
- Followups (separate work, not blocking Phase 6):
  - Stand up a `scripts/daily_backup.sh` covering `pg_dump | gzip` plus `tar czf` of the upload root, retention ~14 days, run by user crontab.
  - Provision Backblaze B2 and rsync `~/backups/` off-server.
  - Once both land, this note is superseded; uploads inherit the same RPO as Postgres.

---

## Phase 6: Tests and verification

Backend smoke tests:

- `tests/test_event_documents_smoke.py` covers all Phase 2 endpoints, the CHECK constraint, the size and type validation, and the soft-delete behavior.
- `tests/test_events_smoke.py` extended to confirm `document-counts` response shape and that document rows do not leak into existing event responses.

Frontend verification:

- `cd frontend && npm run build` succeeds with no new warnings.
- Manual browser pass for each tab on Chrome and Safari. Safari specifically because HEIC uploads from iPhone are an expected daily flow.
- Pipeline card badge renders correctly when at least one event has an unpaid invoice.

Smoke command set:

```bash
venv/bin/python tests/test_event_documents_smoke.py
venv/bin/python tests/test_events_smoke.py
cd frontend && npm run build
```

---

## Open questions (not blocking)

- Hard-delete sweep cadence for soft-deleted documents and orphaned local files. Defer to a later cleanup job.
- When upload volume grows, migrate local files to Backblaze B2 or another object store by adding an object-storage implementation behind `services/document_storage.py`.
- If backups are not extended in Phase 5, decide the acceptable durability window for local uploaded files.
- Whether to expose document counts on every kanban card, or only outstanding invoices. Phase 5 picks "only outstanding invoices on cards"; revisit if staff ask for more.
- Whether HEIC files should be auto-converted to JPG on upload for in-browser preview. Out of scope for v1, parked here.
