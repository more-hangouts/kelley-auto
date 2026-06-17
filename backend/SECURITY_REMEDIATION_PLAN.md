# Security Remediation Plan

Source: `SECURITY_AUDIT.md` (2026-05-13, gitignored). This plan is committed; the audit itself is not.

## Purpose

A sequenced, slice-by-slice plan to close every finding from the audit without breaking production. Each slice is independently shippable, smoke-verified, and rollback-able, mirroring the Phase 9 sub-slice rhythm already used in this repo.

## Operating rules

Carry these into every slice — they are the difference between "fix" and "outage":

1. Work happens on the VPS. Build and lint locally; verify in a real browser against `admin.shopbellasxv.com` after rebuild. No local dev server.
2. End every slice with a readable, smoke-verified commit and `git push` before moving to the next.
3. Run smokes serially. Several touch singleton or numbering rows and step on each other in parallel.
4. New disk-write paths need a `ReadWritePaths=` entry on the systemd unit. "CORS-looking" errors are often disguised 500s from filesystem denials.
5. Validate every schema migration with a real `INSERT` before declaring done.
6. Resend / retry verbs must re-dispatch the side effect, not just bump a timestamp.
7. Customer-facing copy: no em dashes, vary phrasing beyond "X, and Y".
8. Never commit `SECURITY_AUDIT.md` or `*.zip`. Both are gitignored.

## Ordering rationale

- **Phase A** first: smallest blast radius, highest leverage. One dependency CVE plus housekeeping in one safe pass.
- **Phase B**: durable rate limiting on public surfaces. Requires Redis, so it has its own setup slice.
- **Phase C**: data-at-rest encryption + integrity triggers. Touches the DB additively only.
- **Phase D**: auth/session refactors. Highest UX-regression risk, sequenced after rate limits exist.
- **Phase E**: file upload + security headers. Small surface per endpoint.
- **Phase F**: VPS hardening. SSH/sudo changes can lock us out; runs after app stack is stable so we don't conflate failures.
- **Phase G**: retention and TTL polish.
- **Phase H**: multi-tenant pivot. Biggest change, hardest to undo. **Hard gate: no second tenant ships until H5 closes.**

## Tracking

Mark a row `in progress` when starting, `shipped YYYY-MM-DD` when committed and smoke-passed on prod.

**Audit closure status (2026-05-15):** Phases A through E + G are fully shipped. Phase F is 6/7 shipped with F3 (SSH source IP allowlist / VPN gate) explicitly deferred and accepted as residual risk — see the F3 slice note. Phase D is fully shipped as of D3. Parking lot is closed-or-accepted (see notes there for each item). Phase H was rescoped at H1 (2026-05-15) from a multi-tenant pivot (tenant_id + RLS) to a per-tenant deployment model — one hardened codebase, separate DB / .env / domains / secrets per client. The original 5-slice H1-H5 collapsed to 3 slices (H1 strategy reset + handoff rewrite, H2 rebrand surface audit, H3 client deployment runbook + gate) and all three shipped 2026-05-15. **Plan status: closed.** Residual carries: F3 SSH exposure (accepted), Liquid Web ticket on lwadmin-JM831T (pending external response), test-infra import-ordering (deferred eng debt, not security-blocking), and the workflow-vocabulary generalization slice that the H3 Decision Gate would require for any different-vertical client. The next deployment uses [`docs/CLIENT_DEPLOYMENT_RUNBOOK.md`](docs/CLIENT_DEPLOYMENT_RUNBOOK.md) as its operating procedure.

| Phase | Slice | Title | Severity rollup | Status |
|---|---|---|---|---|
| A | A1 | FastAPI / Starlette CVE bump | HIGH | shipped 2026-05-13 |
| A | A2 | Refund route to admin scope | MEDIUM | shipped 2026-05-13 |
| A | A3 | nginx global TLS + sysctl tighten | LOW | shipped 2026-05-13 |
| A | A4 | SSH ciphers / MACs modernize | LOW | shipped 2026-05-13 |
| A | A5 | CI: SHA-pin actions, least-privilege token | MEDIUM | shipped 2026-05-13 |
| B | B1 | Redis-backed limiter foundation | HIGH (enabler) | shipped 2026-05-13 |
| B | B2 | Admin login + sales PIN limiter | HIGH | shipped 2026-05-13 |
| B | B3 | Booking widget + confirmation-code limiter | HIGH | shipped 2026-05-13 |
| B | B4 | Portal token endpoint limiter | MEDIUM | shipped 2026-05-13 |
| C | C1 | Encrypt `integration_tokens` columns | HIGH | shipped 2026-05-13 |
| C | C2 | Redact webhook headers + retention sweep | MEDIUM | shipped 2026-05-13 |
| C | C3 | Quote signature immutability + HMAC | MEDIUM | shipped 2026-05-14 |
| C | C4 | Audit tables append-only triggers | LOW | shipped 2026-05-14 |
| D | D1 | Confirmation-code entropy boost | HIGH | shipped 2026-05-14 |
| D | D2 | Server-side logout / token revocation | MEDIUM | shipped 2026-05-14 |
| D | D3 | Bearer tokens out of localStorage | MEDIUM | shipped 2026-05-15 |
| D | D4 | Password reset flow implementation | MEDIUM | shipped 2026-05-14 |
| D | D5 | python-jose → pyjwt migration | HIGH | shipped 2026-05-14 |
| D | D6 | passlib → direct bcrypt or argon2 | MEDIUM | shipped 2026-05-14 |
| E | E1 | Force attachment disposition for documents | MEDIUM | shipped 2026-05-14 |
| E | E2 | Magic-byte upload validation | LOW | shipped 2026-05-14 (with E1) |
| E | E3 | App-level security headers middleware | MEDIUM | shipped 2026-05-14 |
| F | F1 | systemd sandbox directives | MEDIUM | shipped 2026-05-14 |
| F | F2 | Reduce passwordless sudo | HIGH | shipped 2026-05-14 |
| F | F3 | SSH source IP allowlist or VPN gate | HIGH | deferred 2026-05-15 (see slice note) |
| F | F4 | File / dir permission tightening | MEDIUM | shipped 2026-05-14 |
| F | F5 | fail2ban nginx jails | LOW | shipped 2026-05-14 |
| F | F6 | Verify certbot privkey target permissions | MEDIUM | shipped 2026-05-14 (verified correct, no changes) |
| F | F7 | pg_hba.conf local-auth tighten | HIGH | shipped 2026-05-14 |
| G | G1 | Booking token TTL reduction + revocation | LOW | shipped 2026-05-14 |
| G | G2 | Attendance geo / IP retention sweep | LOW | shipped 2026-05-14 |
| G | G3 | Standardize soft-delete policy | MEDIUM | shipped 2026-05-14 |
| H | H1 | Strategy reset + handoff rewrite | HIGH | shipped 2026-05-15 |
| H | H2 | Rebrand surface audit | MEDIUM | shipped 2026-05-15 |
| H | H3 | Client deployment runbook + gate | HIGH | shipped 2026-05-15 |

---

## Phase A — Quick wins, low blast radius

### Slice A1 — FastAPI / Starlette CVE bump

**Goal:** close CVE-2024-47874 (multipart memory DoS) by lifting Starlette to ≥ 0.40.0 via FastAPI.

**Findings:** [HIGH] Starlette multipart memory DoS.

**Files touched:** `requirements.txt`.

**Approach:** bump `fastapi==0.115.0` to the latest `0.115.x` that pulls Starlette ≥ 0.40.0. Rebuild venv, restart service.

**Smoke tests (serial):**
- `pytest -q` clean.
- VPS: `systemctl restart bellas-xv-api && journalctl -u bellas-xv-api -n 200 --no-pager` — no import or startup errors.
- `curl -fsS https://admin.shopbellasxv.com/api/health` returns 200.
- Browser: admin login, create a booking with a photo upload, submit a sales quote.
- `pip show starlette` reports ≥ 0.40.0.

**Rollback:** revert the `requirements.txt` commit, `pip install -r requirements.txt`, restart service.

**Acceptance:** Starlette ≥ 0.40.0 installed and all smokes pass.

### Slice A2 — Refund route to admin scope

**Goal:** require admin scope on refund creation, matching `void_payment` and `delete_payment`.

**Findings:** [MEDIUM] Refund creation is not admin-only.

**Files touched:** `api/routers/payments.py`, `tests/api/test_payments.py` (or equivalent).

**Approach:** add `require_admin_scope` dependency to `record_refund`. Add a negative test that a non-admin user gets 403; keep the positive admin test green.

**Smoke tests:** `pytest tests/api/test_payments.py -q`; browser: admin refund a test invoice → 200, non-admin → 403.

**Rollback:** revert the single commit.

**Acceptance:** refund returns 403 for non-admin and 200 for admin.

### Slice A3 — nginx global TLS + sysctl tighten

**Goal:** remove TLS 1.0 / 1.1 from the global nginx stanza and disable `kernel.unprivileged_userns_clone`.

**Findings:** [LOW] nginx global TLS 1.0/1.1; [LOW] `unprivileged_userns_clone=1`.

**Files touched:** `/etc/nginx/nginx.conf`, `/etc/sysctl.d/99-bellas-hardening.conf` (new).

**Approach:** set `ssl_protocols TLSv1.2 TLSv1.3;` in `nginx.conf`. Add a sysctl drop-in with `kernel.unprivileged_userns_clone=0`.

**Smoke tests:**
- `nginx -t && systemctl reload nginx`.
- `sysctl --system && sysctl kernel.unprivileged_userns_clone` reports `0`.
- `curl -vI https://admin.shopbellasxv.com 2>&1 | grep -i 'TLSv1\.'` shows 1.2 or 1.3.
- Browser smoke: load the admin site, the customer portal, and the booking widget.

**Rollback:** revert the two files, reload nginx, `sysctl --system`.

**Acceptance:** TLS handshake offers 1.2/1.3 only; sysctl flips persist across reboot.

### Slice A4 — SSH ciphers / MACs modernize

**Goal:** drop `hmac-sha1`, `umac-64`, and SHA1 GSS KEX algorithms from sshd.

**Findings:** [LOW] SSH permits legacy MACs/GSS.

**Files touched:** `/etc/ssh/sshd_config.d/10-bellas-modern-crypto.conf` (new drop-in, no edit to base file).

**Pre-flight (critical — do not skip):** open a second SSH session and leave it logged in before reloading sshd. Verify config syntax with `sshd -t` before reload.

**Approach:** drop-in with modern `Ciphers`, `MACs`, `KexAlgorithms`. Disable `GSSAPIAuthentication` and `GSSAPIKeyExchange` if unused. Reload, not restart.

**Smoke tests:**
- `sshd -t` clean.
- `systemctl reload ssh`.
- From the second already-open session: `sshd -T | grep -Ei '^(ciphers|macs|kex)'` shows modern-only.
- Open a third fresh SSH connection from a known IP — must succeed before closing existing sessions.

**Rollback:** delete the drop-in, `systemctl reload ssh` from the still-open second session.

**Acceptance:** new SSH connections work, weak MACs/KEX no longer offered.

### Slice A5 — CI SHA-pin actions, least-privilege token

**Goal:** pin third-party GitHub Actions to commit SHA and restrict the workflow token.

**Findings:** [MEDIUM] Actions pinned to mutable tags.

**Files touched:** `.github/workflows/smoke.yml`.

**Approach:** replace `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4` with `@<full-sha>  # vX.Y.Z` comments. Add `permissions: contents: read` at the workflow level. Optionally enable Dependabot for `github-actions` to keep SHAs bumped.

**Smoke tests:** push a no-op commit and confirm the workflow run is green.

**Rollback:** revert the workflow commit.

**Acceptance:** smoke workflow green; default token cannot escalate.

---

## Phase B — Durable app-level rate limiting

> Single biggest reduction in brute-force / abuse risk. Requires Redis, which becomes a new infra dependency.

### Slice B1 — Redis-backed limiter foundation

**Goal:** stand up a Redis instance and a reusable limiter dependency suitable for all later B/D slices.

**Findings:** enabler for B2-B4, D1, D2.

**Files touched:** `requirements.txt`, `api/dependencies/rate_limit.py` (new), `api/server.py`, `.env.example`, `/etc/systemd/system/bellas-xv-api.service` (`ReadWritePaths` only if Redis uses Unix socket), nginx (no change unless we expose `/metrics`).

**Approach:** install `redis-server` via apt, bind to `127.0.0.1`. Add `redis-py` to `requirements.txt`. Implement a single `rate_limit(key_fn, limit, window)` dependency with a graceful fail-open behavior gated by a `RATE_LIMIT_FAIL_OPEN` env flag (default `false` in prod, `true` until B2 ships so partial deploys don't 503 the site). Emit metrics-friendly log lines on limit hits.

**Smoke tests:**
- `redis-cli ping` returns `PONG`.
- New `pytest tests/api/test_rate_limit.py -q` covers: under limit → 200, over limit → 429, Redis down with fail-open → 200, Redis down with fail-closed → 503.
- VPS: restart service, `journalctl -u bellas-xv-api -n 200 --no-pager` no errors.

**Rollback:** revert the limiter commit, leave Redis installed (harmless).

**Acceptance:** Redis healthy on prod, limiter unit tests green, no route actually wired yet.

### Slice B2 — Admin login + sales PIN limiter

**Goal:** add Redis per-IP and per-identifier limits to `/api/auth/login` and the sales PIN unlock route, on top of existing row lockouts.

**Findings:** [HIGH] Admin login has no app-side throttle. [HIGH] Sales PIN brute-force per-row only.

**Files touched:** `api/routers/auth.py`, `api/routers/sales_auth.py`, `services/sales_auth.py` (config only).

**Approach:** wire the B1 limiter. Buckets: per-IP 10/min, per-email 5/min, global PIN 60/min (alert threshold). Flip `RATE_LIMIT_FAIL_OPEN=false` in prod env after this slice.

**Smoke tests:**
- Log in normally — works.
- Fire 12 bad logins from one IP — 11th is 429.
- Sales PIN: 6 bad attempts from same IP across 6 different usernames — sixth IP-bucket attempt is 429.
- Row lockout still triggers at 5 bad attempts on the same account (existing behavior).

**Rollback:** comment out the limiter deps, restart, investigate.

**Acceptance:** 429 responses observed under load test; row lockout still works; legitimate logins unaffected.

### Slice B3 — Booking widget + confirmation-code limiter

**Goal:** put limits on every public booking write route and on confirmation-code lookups.

**Findings:** [HIGH] No app-level rate limiting on booking endpoints. Partial mitigation for [HIGH] confirmation-code entropy until D1 ships.

**Files touched:** `api/routers/booking.py` (creates, telemetry, reschedule, cancel, profile attach, token submit).

**Approach:** apply the limiter dep to each public POST/PATCH route with per-IP buckets sized to the action (telemetry generous, writes tight, confirmation-code very tight per email).

**Smoke tests:**
- Single booking via widget on prod — works.
- Scripted 30 booking POSTs from one IP — 429s start.
- Confirmation-code attach: 6 wrong codes for one email from one IP — 6th is 429.
- Existing booking abandon telemetry still records to `appointment_session_events`.

**Rollback:** comment out the deps on touched routes.

**Acceptance:** real bookings work; flood gets 429; abandon telemetry unchanged.

### Slice B4 — Portal token endpoint limiter

**Goal:** rate-limit invoice/quote portal token lookup and signature submit.

**Findings:** [MEDIUM] Public portal tokens. [LOW] Long-lived booking tokens (partial mitigation pending G1).

**Files touched:** `api/routers/portal.py`, `api/redis_rate_limit.py`, `tests/test_portal_rate_limit_smoke.py`. Booking reschedule/cancel token routes were already covered by B3's `booking_token_ip` bucket.

**Approach:** per-IP and per-token-key buckets. Keep the existing in-process limiter as defense in depth; new limiter is the durable layer.

**Smoke tests:** valid portal link works; 30 invalid-key probes from one IP get 429; signature submit on a valid quote still works once.

**Rollback:** revert deps on these routes.

**Acceptance:** invalid-key enumeration is rate-limited; valid customer flows unchanged.

---

## Phase C — Data-at-rest encryption and integrity

### Slice C1 — Encrypt `integration_tokens` columns

**Goal:** stop storing OAuth access/refresh tokens in plaintext.

**Findings:** [HIGH] OAuth/integration tokens plaintext.

**Files touched:** `database/migrations/06X_integration_tokens_encrypt.py` (new), `services/integration_tokens.py` (or current owner), `.env` (add `INTEGRATION_TOKEN_KEY`).

**Approach:** generate a 32-byte key, store in `.env` (mode 600). Use `cryptography.fernet` or `pgcrypto` envelope encryption — pick one and document choice in the migration docstring. Migration: add `access_token_ciphertext BYTEA`, `refresh_token_ciphertext BYTEA`, backfill by encrypting current values, drop old columns in a follow-up migration only after one full deploy cycle confirms reads work.

**Smoke tests:**
- Migration runs cleanly on a copy of prod data (use a `--dry-run` migration runner or staging DB).
- `INSERT` + read-back round-trip via the service layer matches the plaintext input.
- Restart service, trigger one webhook that requires the integration token, verify it still calls out successfully.

**Rollback:** the old columns remain populated until the follow-up migration; revert service-layer commit and old columns are still authoritative.

**Acceptance:** new writes go to ciphertext; reads return correct plaintext; integration still works in prod for at least one full sync cycle.

### Slice C2 — Redact webhook headers + retention sweep

**Goal:** strip sensitive headers before insert and prune old `webhook_events` rows.

**Findings:** [MEDIUM] Webhook payload/headers plaintext.

**Files touched:** `services/webhook_ingest.py` (or current owner), `database/migrations/06X_webhook_events_retention.py` (new), `cron_run_state` row for the new sweep.

**Approach:** maintain an allowlist of headers to keep (`content-type`, `user-agent`, provider message id). Drop `authorization`, `x-*-signature`, `cookie`, anything containing `token` or `key`. Add a retention sweep (default 90 days) gated by `cron_run_state`, runnable via the existing scheduler.

**Smoke tests:**
- Send a webhook with a fake `Authorization: Bearer xyz` header — DB row's `headers` JSONB has no `authorization` key.
- Backfill: run the sweep manually with `--max-age-days=9999` (no-op), then `--max-age-days=90` (prunes), confirm row counts.
- Verify the sweep updates `cron_run_state` like other recurring jobs.

**Rollback:** revert the redaction commit; the retention migration is additive (can leave in place).

**Acceptance:** new rows have redacted headers; sweep prunes old rows on schedule.

### Slice C3 — Quote signature immutability + HMAC

**Goal:** prevent silent rewrite of signed quote rows and capture an HMAC of signed content.

**Findings:** [MEDIUM] Quote signatures mutable by schema.

**Files touched:** `database/migrations/06X_quote_signature_immutable.py` (new), `services/quote_service.py` (HMAC stamp on sign), `database/migrations/06X_quote_signature_hmac.py` (new column).

**Approach:** mirror the `prevent_catalog_public_code_update` trigger from migration 044. Add a `BEFORE UPDATE` trigger that raises if any of `signature_base64`, `signature_signed_at`, `signature_ip`, `signature_name`, `signature_user_agent` is being changed from a non-null value. Add `signature_hmac BYTEA` column, written at sign time as HMAC-SHA256 over the canonical signed payload using `QUOTE_SIGNATURE_KEY`.

**Smoke tests:**
- Run the migration on a copy of prod, attempt an `UPDATE quotes SET signature_base64='x' WHERE id=...` — raises.
- Sign a test quote via the customer portal, verify `signature_hmac` is non-null and recomputable.
- Existing approve-quote idempotency: re-submit the same signature → no-op, no exception (approve path must not re-set the columns).

**Rollback:** drop the trigger; revert service-layer HMAC stamp.

**Acceptance:** signed rows cannot be mutated; HMAC verifies on read.

### Slice C4 — Audit tables append-only triggers

**Goal:** schema-enforce append-only on `activity_log`, `staff_punch_audit_events`, `time_off_decision_events`, `refund_events`, `event_status_change_events`.

**Findings:** [LOW] Audit tables append-only by convention only.

**Files touched:** `database/migrations/06X_audit_tables_append_only.py` (new).

**Approach:** add `BEFORE UPDATE OR DELETE` triggers on each table that `RAISE EXCEPTION`. Document the one-line exception path for ops to follow if a real delete is ever needed (drop trigger, delete, recreate trigger).

**Smoke tests:**
- Run migration on copy of prod.
- `DELETE FROM activity_log WHERE id = 1` — raises.
- Application paths that insert into these tables (refund, time off decision, status change) — unaffected.

**Rollback:** drop the triggers.

**Acceptance:** writes still succeed; updates and deletes raise.

---

## Phase D — Auth and session refactors

### Slice D1 — Confirmation-code entropy boost

**Goal:** raise public confirmation-code entropy from ~30 bits to ≥ 96 bits, or replace with a signed token for self-service writes.

**Findings:** [HIGH] Confirmation codes ~30 bits.

**Files touched:** `services/booking_service.py`, `database/migrations/06X_appointments_confirmation_code_widen.py` (if widening column), customer email templates that print the code.

**Approach:** decide between (a) widening the alphabet/length to 20 chars URL-safe ≈ 120 bits, or (b) keeping a short human code for display but requiring a signed token for the profile-attach endpoint. Option (b) is friendlier to customers; (a) is simpler. Either way, keep B3 rate limiting in place — entropy and limits are layered defenses.

**Smoke tests:**
- New booking emits a code that decodes/validates correctly.
- Existing in-flight bookings continue to work (backwards-compat: accept both old and new format for a deprecation window, or rotate codes on read).
- Customer email previews still render readable codes (or token-links).

**Rollback:** revert; old codes are still valid in DB.

**Acceptance:** new codes are ≥ 96 bits effective; old codes accepted during transition; phase out within 60 days of last issuance.

### Slice D2 — Server-side logout / token revocation endpoint

**Goal:** make logout actually invalidate the token.

**Findings:** [MEDIUM] Logout is client-only.

**Files touched:** `api/routers/auth.py`, `database/auth.py`, `frontend/src/contexts/AuthContext.jsx`, `frontend/src/contexts/SalesAuthContext.jsx`.

**Approach:** add `POST /api/auth/logout` that bumps `users.token_version` for the current user. Frontend calls it before clearing localStorage. Existing `tv` check in JWT decode already rejects stale tokens.

**Smoke tests:**
- Log in, copy the token, click logout — replaying the token returns 401.
- Sales logout follows the same path.
- Logout when offline: client still clears state, server bump retried opportunistically (or accepted as best-effort).

**Rollback:** revert; client-only logout returns.

**Acceptance:** copied tokens stop working after logout.

### Slice D3 — Bearer tokens out of localStorage

**Goal:** move admin and sales bearer tokens from localStorage to HttpOnly Secure SameSite cookies, or shorten TTL aggressively if cookie move is too invasive this round.

**Findings:** [MEDIUM] Bearer tokens in localStorage.

**Files touched:** `frontend/src/contexts/AuthContext.jsx`, `frontend/src/contexts/SalesAuthContext.jsx`, `api/routers/auth.py`, `api/routers/sales_auth.py`, `api/server.py` (cookie config), nginx if any path-stripping is involved.

**Approach (preferred):** server sets cookie on login, removes on logout (D2). Frontend drops the `Authorization` header for admin/sales paths and relies on cookies. Requires CSRF token for state-changing routes — add a double-submit cookie pattern, since cookies alone are not enough.

**Approach (fallback if cookie scope is too big this slice):** shorten admin token TTL from current value to 30 minutes; rely on D2 + refresh on activity.

**Smoke tests:**
- Cookie path: login sets HttpOnly cookie (DevTools confirms). Subsequent requests have no Authorization header. State-changing routes succeed when CSRF token matches; fail with 403 when missing.
- Fallback: tokens expire after 30 minutes; UI prompts re-login.

**Rollback:** revert all touched files; localStorage tokens return.

**Acceptance:** chosen approach functions end-to-end with admin, sales, and customer portal flows.

### Slice D4 — Password reset flow implementation

**Goal:** implement the password-reset endpoints the schema already supports.

**Findings:** [MEDIUM] No password reset flow despite schema.

**Files touched:** `api/routers/auth.py`, `services/password_reset.py` (new or fill in), `frontend/src/pages/PasswordResetRequest.jsx` (new), `frontend/src/pages/PasswordResetConfirm.jsx` (new), customer/admin email template.

**Approach:** request endpoint accepts email, always returns 200 (no account enumeration), enqueues an email with a URL containing a `secrets.token_urlsafe(32)` value. DB stores the hashed token, expiry (15 min), `used_at`. Confirm endpoint validates, sets new password via passlib/bcrypt, marks `used_at`. Throttle requests via the B1 limiter (1/min/email, 5/min/IP).

**Smoke tests:**
- Request reset for an existing user → email arrives → link sets new password → old password no longer works.
- Request reset for a nonexistent email → still returns 200; no email sent; no DB row leak.
- Re-use of a consumed token → 400.
- Expired token → 400.

**Rollback:** revert frontend + backend commits; DB rows are harmless.

**Acceptance:** flow works end-to-end and does not enable enumeration.

### Slice D5 — python-jose → pyjwt migration

**Goal:** retire `python-jose` (and the `ecdsa` transitive) by moving JWT encode/decode to `pyjwt[crypto]`.

**Findings:** [HIGH] python-jose CVEs; [HIGH] ecdsa WONTFIX timing advisory.

**Files touched:** `requirements.txt`, `database/auth.py`, `services/booking_tokens.py`, any other JWT call sites.

**Approach:** replace `from jose import jwt` with `import jwt`. Keep `algorithms=['HS256']` pinned on decode. Confirm exp/iat handling matches. Update tests to exercise both happy path and tampered-token cases.

**Smoke tests:**
- `pytest -q` clean, especially auth tests.
- Existing tokens in the wild still decode (HS256 + same secret is wire-compatible).
- New tokens produced by pyjwt decode cleanly.
- Tampered token rejected.

**Rollback:** revert; old library is still installable.

**Acceptance:** no `from jose` imports remain; `pip uninstall python-jose ecdsa` succeeds without breaking anything.

### Slice D6 — passlib → direct bcrypt or argon2

**Goal:** remove the `passlib==1.7.4` pin that holds back `bcrypt`.

**Findings:** [MEDIUM] `passlib` unmaintained, pins old bcrypt.

**Files touched:** `requirements.txt`, all `passlib.context.CryptContext` call sites (likely in `database/auth.py` and the password reset service).

**Approach:** replace with direct `bcrypt` (simpler migration) or `argon2-cffi` (stronger default). Read existing hashes via the legacy verify path during a transition; rehash on next successful login.

**Smoke tests:**
- Existing users log in with their existing passwords.
- After first successful login, the stored hash changes to the new format.
- New password reset via D4 writes new-format hashes.

**Rollback:** revert; old hashes still valid.

**Acceptance:** all logins still work; new hashes use the new library; old hashes phased out as users log in.

---

## Phase E — Upload and header hardening

### Slice E1 — Force attachment disposition for documents

**Goal:** stop allowing inline rendering of user-uploaded documents.

**Findings:** [MEDIUM] Document downloads allow inline.

**Files touched:** `api/routers/event_documents.py`.

**Approach:** remove the `disposition` query param, hard-set `Content-Disposition: attachment; filename=...`. If a preview UX is needed for PDFs, generate previews server-side instead of inline-serving the raw file.

**Smoke tests:** download a doc → browser saves rather than renders. Existing UI download buttons still work.

**Rollback:** revert.

**Acceptance:** no inline rendering of uploaded documents.

### Slice E2 — Magic-byte upload validation

**Goal:** validate uploads by magic bytes, not just extension and declared content-type.

**Findings:** [LOW] Upload validation trusts ext/content-type.

**Files touched:** `api/routers/event_documents.py`, `services/document_upload.py` (or where the write happens).

**Approach:** read first N bytes, check against known signatures for PDF/JPEG/PNG/WebP/HEIC/DOCX. Reject mismatches with 415. Store a server-derived content type.

**Smoke tests:** rename a `.exe` to `.pdf` and upload → 415. Genuine PDF/JPEG/PNG/DOCX → 200. Existing valid documents still downloadable.

**Rollback:** revert.

**Acceptance:** mismatches rejected; valid uploads unaffected.

### Slice E3 — App-level security headers middleware

**Goal:** defense-in-depth: have the app emit baseline security headers itself, not only nginx.

**Findings:** [MEDIUM] App does not set security headers itself.

**Files touched:** `api/server.py`, possibly `api/middleware/security_headers.py` (new).

**Approach:** add middleware setting HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, and a starter CSP (likely report-only first). Do not override headers already set by nginx — use `setdefault` style.

**Smoke tests:** `curl -sI http://127.0.0.1:8000/api/health` (bypassing nginx) shows the headers. Browser smoke through nginx: no duplicate header warnings in DevTools.

**Rollback:** revert.

**Acceptance:** direct-to-uvicorn requests carry baseline headers; nginx-served responses unchanged or unaffected.

---

## Phase F — VPS hardening

> SSH/sudo changes can lock us out. F2, F3, F7 each have explicit pre-flight steps. Always keep a second session open.

### Slice F1 — systemd sandbox directives

**Goal:** lower the systemd-analyze exposure score on `bellas-xv-api.service` from 7.8 to below 5.0.

**Findings:** [MEDIUM] systemd service broadly exposed.

**Files touched:** `/etc/systemd/system/bellas-xv-api.service.d/hardening.conf`; app/test follow-ups in `api/routers/business_profile.py`, `tests/test_business_profile_smoke.py`, `tests/test_clock_in_smoke.py`, `tests/test_upload_validation_smoke.py`.

**Approach:** add a drop-in, not a base-unit edit. Tighten the broadly compatible directives first: kernel/cgroup/hostname/clock protections, `RestrictNamespaces=true`, `RestrictSUIDSGID=true`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `UMask=0077`, empty `CapabilityBoundingSet=` / `AmbientCapabilities=`, `SystemCallArchitectures=native`, and `RemoveIPC=true`. Keep existing `ReadWritePaths` entries intact. Defer `MemoryDenyWriteExecute=true`, `SystemCallFilter=@system-service`, and `ProtectProc=invisible` to a future report-mode/compatibility pass because Pillow/WeasyPrint/cairo/libxml2 and uvicorn process behavior need deeper verification.

**Smoke tests:**
- `systemd-analyze verify /etc/systemd/system/bellas-xv-api.service` clean.
- `systemctl daemon-reload && systemctl restart bellas-xv-api`.
- `journalctl -u bellas-xv-api -n 300 --no-pager` — no syscall denials or path errors.
- Browser smoke: admin login, booking write, photo upload, document upload (these are the paths most likely to hit syscall or path restrictions).
- `systemd-analyze security bellas-xv-api.service` < 5.0.

**Rollback:** revert the unit file, `systemctl daemon-reload && systemctl restart`.

**Acceptance:** service starts, all smokes pass, score reduced.

### Slice F2 — Reduce passwordless sudo

**Goal:** replace `NOPASSWD: ALL` for `luis` with command-specific sudo or password-protected interactive sudo.

**Findings:** [HIGH] Full passwordless sudo.

**Files touched:** `/etc/sudoers.d/luis`, decide on `lwadmin-JM831T` separately (provider admin).

**Pre-flight:**
- Confirm `luis` has a working password set (`sudo passwd luis` if not) and that the password is in the password manager.
- Open a second SSH session with sudo already authenticated (`sudo -v`).
- Confirm console / serial access through the provider exists as a last-resort recovery path.

**Approach:** keep a narrow `NOPASSWD` for the specific automation commands that need it (systemctl restart of the app service, journalctl tail). Everything else requires a password. Use `visudo -f /etc/sudoers.d/luis` so syntax errors don't lock you out.

**Smoke tests:**
- From a fresh SSH session: `sudo systemctl restart bellas-xv-api` works without password (if allowlisted) or with password.
- `sudo apt update` prompts for password.
- Decide on `lwadmin-JM831T`: contact provider before changing or removing.

**Rollback:** restore the original `/etc/sudoers.d/luis` from a backup taken in the pre-flight (`cp /etc/sudoers.d/luis /etc/sudoers.d/luis.bak` before editing).

**Acceptance:** day-to-day deploy commands still work; arbitrary `sudo` requires password.

### Slice F3 — SSH source IP allowlist or VPN gate

**Status: deferred 2026-05-15.** SSH exposure remains accepted temporarily; fail2ban, modern SSH crypto, scoped sudo, and provider out-of-band console reduce risk until a VPN / IP-allowlist architecture is scheduled. Revisit after Phase D and before Phase H multi-tenant work, or sooner if abuse rate on the sshd jail rises materially.

**Goal:** stop exposing SSH to the entire internet.

**Findings:** [HIGH] SSH on port 22 open to world.

**Files touched:** UFW rules and/or provider firewall, optionally `/etc/ssh/sshd_config.d/`.

**Pre-flight decision:** pick one of:
- A. Static-IP allowlist (only if you have a stable admin IP).
- B. Provider firewall + VPN (preferred long-term).
- C. Bastion / jump host.

**Pre-flight checks (mandatory):**
- Verify provider out-of-band console works.
- Confirm `fail2ban` is healthy as a fallback.
- Open a second SSH session before changing firewall rules.

**Approach:** for option A: `ufw allow from <ip> to any port 22 proto tcp`, then `ufw delete allow 22/tcp`. For option B: add VPN, allow 22 only from VPN subnet. For option C: stand up bastion, repoint allow rule to bastion IP.

**Smoke tests:**
- New SSH from an allowlisted source: works.
- New SSH from a non-allowlisted source: connection refused/timeout.
- Web traffic (80/443) unaffected.

**Rollback:** `ufw allow 22/tcp` from console if locked out.

**Acceptance:** SSH reachable only from intended sources.

### Slice F4 — File / dir permission tightening

**Goal:** reduce traversability of `/home/luis` and the repo.

**Findings:** [MEDIUM] Home and repo group/world traversable.

**Files touched:** filesystem only.

**Approach:** `chmod 750 /home/luis`. For the repo, choose 750 or 755 depending on whether nginx user needs to read static assets directly (it likely serves `frontend/dist` and needs read). Add nginx user to a shared group if 750 chosen. `chmod 750 /home/luis/bellas_xv/logs`.

**Smoke tests:**
- Service restarts and runs (still readable by app user).
- Nginx still serves frontend assets.
- Local user other than `luis` cannot `ls /home/luis`.

**Rollback:** restore prior modes.

**Acceptance:** restricted modes hold; site loads.

### Slice F5 — fail2ban nginx jails

**Goal:** add nginx-targeted jails for repeated 4xx and credential-stuffing patterns.

**Findings:** [LOW] No web/nginx fail2ban jails.

**Files touched:** `/etc/fail2ban/jail.d/nginx.local` (new).

**Approach:** enable `nginx-http-auth`, `nginx-botsearch`, and a custom jail for repeated 401s on `/api/auth/login` and the sales PIN route. Conservative thresholds; aliases for trusted IPs in `ignoreip`.

**Smoke tests:** `fail2ban-client status nginx-http-auth` lists the jail; trigger a few 401s from a test IP and confirm the ban appears in `fail2ban.log`.

**Rollback:** delete the jail file, `fail2ban-client reload`.

**Acceptance:** new jails active; legitimate traffic uninterrupted.

### Slice F6 — Verify certbot privkey target permissions

**Goal:** confirm the live `privkey.pem` symlink resolves to a 600 root file.

**Findings:** [MEDIUM] Certbot symlink path shows 777.

**Files touched:** filesystem audit only; correct any wrong modes.

**Approach:** `readlink -f /etc/letsencrypt/live/admin.shopbellasxv.com/privkey.pem`, `stat` the target. If not `600 root:root`, fix and document why. Confirm `/etc/letsencrypt/archive` is `700 root:root`.

**Smoke tests:** key target mode is `600 root:root`; certbot renewal `certbot renew --dry-run` succeeds.

**Rollback:** none required if no change made.

**Acceptance:** key material is not world-accessible.

### Slice F7 — pg_hba.conf local-auth tighten

**Goal:** ensure local Unix users cannot connect to the app's DB role just by being `luis`.

**Findings:** [HIGH] pg_hba peer auth for all local users.

**Files touched:** `/etc/postgresql/16/main/pg_hba.conf`.

**Pre-flight:** open a second psql session as `postgres` peer before editing.

**Approach:** keep `postgres` peer; require `scram-sha-256` for `bellas_xv_user` on local socket; ensure DATABASE_URL contains the password (already does).

**Smoke tests:**
- App restarts and queries succeed (uses password).
- `psql -U bellas_xv_user -d bellas_xv` from `luis` shell prompts for password and fails without it.
- `sudo -u postgres psql` still works as peer.

**Rollback:** restore prior pg_hba content from a backup taken in pre-flight, `systemctl reload postgresql`.

**Acceptance:** app works; shell users cannot bypass password.

---

## Phase G — Retention and TTL polish

### Slice G1 — Booking token TTL reduction + revocation

**Goal:** shorten reschedule/cancel/enrichment tokens and add server-side revocation.

**Findings:** [LOW] Booking tokens long-lived JWTs in URLs.

**Files touched:** `services/booking_tokens.py`, possibly a new `booking_token_revocations` table.

**Approach:** drop reschedule/cancel default from 60 days to 14, enrichment from 30 to 7. Add a small table `booking_token_revocations(jti, revoked_at)` and check it at decode time. Bump `jti` claim.

**Smoke tests:** issue token, use it within TTL, revoke, retry → 401. Old long-lived tokens in flight still work until natural expiry (or treat as revoked at cutoff — decide and document).

**Rollback:** revert; old TTLs return.

**Acceptance:** new TTLs in effect; revocation works.

### Slice G2 — Attendance geo / IP retention sweep

**Goal:** null geo/IP/UA on punch rows when their selfie retention expires.

**Findings:** [LOW] Attendance geo outlives selfie retention.

**Files touched:** the existing attendance-retention cron (created in 057-era), plus migration if any new index is needed.

**Approach:** extend the existing selfie cleanup to also null `client_latitude`, `client_longitude`, `client_accuracy_m`, `ip`, `user_agent` on the same cutoff. Keep `distance_to_location_m` and `status` (aggregate).

**Smoke tests:** run cron with a tight cutoff against a copy of prod, confirm columns are nulled and selfie key cleared.

**Rollback:** revert; nulled columns cannot be recovered, but the rollback prevents further nulling.

**Acceptance:** old rows have null PII fields, status/distance retained.

### Slice G3 — Standardize soft-delete policy

**Goal:** classify every table into a documented delete tier and enforce the policy with a guardrail smoke that prevents accidental hard-deletes against financial or CRM-core tables.

**Findings:** [MEDIUM] Soft-delete inconsistent.

**Files touched:** `docs/DATA_RETENTION_AND_DELETE_POLICY.md` (new), `tests/test_delete_policy_guardrail_smoke.py` (new).

**Scope pivot from the original plan:** the audit at ship time confirmed the financial tier (`invoices`, `invoice_invitations`, `quotes`, `quote_invitations`, `payments`, `event_documents`) already has consistent `deleted_at` + filtered reads, and the CRM-core tier (`contacts`, `events`, `appointments`, `event_participants`, `catalog_items`, `special_orders`) has **zero delete code paths** today — no API endpoint, no service helper, no `session.delete()`, no `DELETE FROM`. Adding speculative `deleted_at` columns to tables with no delete UX would have created false coverage and ongoing query burden; the real risk was undocumented policy that would let a future contributor either `session.delete()` a financial row (data loss) or add a CRM delete endpoint without realizing readers don't filter for it.

**Approach:**
- Document the five tiers in `docs/DATA_RETENTION_AND_DELETE_POLICY.md`:
  1. Financial / user-facing soft-delete — service helpers only; reads filter `deleted_at IS NULL`.
  2. CRM core, append-only — no delete path; "hide from list" UX uses status fields.
  3. Retention-managed hard-delete — webhook_events and attendance geo columns; deletion is scheduled, not user-driven.
  4. Operational config hard-delete — admin tables (shifts, holidays, blackouts, availability rules, staff locations).
  5. Rebuild children inside parent transactions — line items / installments / allocations / order discounts.
  6. Special case: `appointment_tried_on_items` — hard-delete with activity_log breadcrumb as the audit substitute.
- Ship `tests/test_delete_policy_guardrail_smoke.py`: an AST-based scan of `services/` and `api/routers/` that resolves every `db.delete()` / `session.delete()` call to its ORM model and every raw `DELETE FROM` to its table name. It compares each call site against an explicit per-file allowlist of expected sites and fails on:
  - Any Tier 1 model (`Invoice`, `InvoiceInvitation`, `Quote`, `QuoteInvitation`, `Payment`, `EventDocument`) appearing as a hard-delete target.
  - Any Tier 2 table (`contacts`, `events`, `appointments`, `event_participants`, `catalog_items`, `special_orders`) appearing in raw `DELETE FROM`.
  - Any new delete site not yet in the allowlist (forces an update to both the policy doc and the smoke when a new tier-3/4/5 site is added).
  - Any allowlisted entry that no longer exists in source (stale allowlist).

**Smoke tests (serial):**
- `venv/bin/python tests/test_delete_policy_guardrail_smoke.py` — passes on a clean tree; was probe-tested at ship time against four scenarios (Tier-1 ORM violation, Tier-2 raw violation, new Tier-4 site not yet allowlisted, new unknown raw DELETE) and failed correctly on all four.
- `venv/bin/python tests/test_audit_append_only_smoke.py` — unchanged, ensures audit-table triggers continue to block UPDATE/DELETE without bypass.

**Rollback:** delete the new doc and smoke; no schema or runtime changes were made.

**Acceptance:** policy documented in `docs/DATA_RETENTION_AND_DELETE_POLICY.md`; guardrail smoke prevents accidental hard-delete additions to Tier 1 (financial) and Tier 2 (CRM-core) tables; allowlist tracks all 14 legitimate delete sites in the current codebase.

---

## Phase H — White-label rollout (per-tenant deployment)

> **Strategy lock-in (H1, 2026-05-15):** new clients ship as independent deployments from one hardened shared codebase — own VPS or isolated DB, own `.env`, own domains, own storage paths, own backups, own secrets (`SECRET_KEY`, `INTEGRATION_TOKEN_KEYS`, `QUOTE_SIGNATURE_KEY`, `RESCHEDULE_TOKEN_SECRET`, `ENRICHMENT_TOKEN_SECRET`). No `tenant_id`, no RLS, no schema retrofit. Data isolation is a property of the operating system, not of every SQL query. See [`WHITE_LABEL_HANDOFF.md`](WHITE_LABEL_HANDOFF.md) for the strategy doc and the future-pivot trigger.

### Slice H1 — Strategy reset + handoff rewrite

**Goal:** commit the per-tenant deployment decision and stop the strategy doc from disagreeing with the security plan.

**Files touched:** `SECURITY_REMEDIATION_PLAN.md` (Phase H rewritten, tracking table collapsed from H1-H5 to H1-H3, closure-status banner updated), `WHITE_LABEL_HANDOFF.md` (rewritten in place as the strategy doc).

**Approach:** the prior Phase H plan assumed a multi-tenant retrofit (tenant_id + RLS) but the existing `WHITE_LABEL_HANDOFF.md` quietly described a fork-and-rebrand model. The two needed to agree before any code or schema work could start. Decision: per-tenant deployment wins for the first 1-3 clients because (1) data isolation is automatic — no SQL filter can leak across a process boundary, (2) the hardening from A through G applies per deployment without any new code paths, (3) a tenant_id retrofit across a codebase built single-tenant is high-risk for low near-term value. The handoff doc is rewritten to make the strategy + boundaries + future-pivot trigger explicit; the security plan loses the multi-tenant H1-H5 framing and gains a 3-slice white-label rollout phase instead.

**Smoke tests:** N/A (docs only).

**Rollback:** revert the doc commit.

**Acceptance:** SECURITY_REMEDIATION_PLAN.md and WHITE_LABEL_HANDOFF.md describe the same strategy in compatible terms; the H1-H3 scope is locked in.

### Slice H2 — Rebrand surface audit

**Goal:** catalogue every client-specific touch point in the codebase without changing behavior, so the first new-client deployment knows exactly what has to be edited and what is already parameterized.

**Files touched:** new `docs/WHITE_LABEL_REBRAND_SURFACE.md`. Zero code or schema changes.

**Approach:** walk the tree and produce a categorized inventory of touch points. Suggested sections: (1) business identity (name, logos, colors, contact info — what's in `business_profile`, what's hardcoded in templates, what lives in static assets), (2) domains + nginx (admin / sales / api / marketing / widget host names; CORS allowlists; certbot certs), (3) workflow language + event statuses (`services/event_workflow.py`, DB CHECK constraints, status labels), (4) booking widget copy + questions (`widgets/`, enrichment contract), (5) invoice / quote / portal templates + email + SMS copy (`templates/`, notification copy in `services/notification_templates.py`), (6) env-driven config (`.env` keys: SMTP, Twilio, Meta Pixel, Google Ads, Plausible, storage paths, secrets), (7) anything else hardcoded that would embarrass the rebrand. For each touch point: current state (parameterized vs hardcoded), action needed (none / parameterize / replace with config row / code edit per deploy), and whether the action belongs in H3 (must be in the runbook) or in a future cleanup slice.

**Hard rule:** audit-only. Do not parameterize anything in H2. Discovering what needs to be configurable before the first new client proves what *actually* needs to be configurable would turn the slice into an open-ended refactor. Leave the work for the H3 runbook plus follow-up slices justified by real second-client requirements.

**Smoke tests:** N/A. Verify by spot-checking that the doc's "hardcoded" list compiles when grep'd against the tree.

**Rollback:** revert the doc commit.

**Acceptance:** inventory is complete enough that an operator can read it and predict the rebrand workload for a hypothetical second client; nothing in the codebase has been modified.

### Slice H3 — Client deployment runbook + go-live gate

**Goal:** turn the post-audit hardening into a repeatable provisioning checklist so a new-client deployment is a documented, verifiable procedure with a security gate that prevents shipping an unhardened copy.

**Files touched:** new `docs/CLIENT_DEPLOYMENT_RUNBOOK.md`. Optional pointer additions to `WHITE_LABEL_HANDOFF.md` and `INFRASTRUCTURE.md`.

**Approach:** step-by-step recipe derived from the current secured codebase. Suggested sections: (1) VPS provisioning + base OS hardening (apply every Phase F slice that shipped — F1 systemd sandbox, F2 scoped sudo, F4 file perms, F5 fail2ban, F6 certbot perms, F7 pg_hba; F3 stays deferred per the parent plan), (2) repo + dependencies (clone, venv, pip install), (3) per-deployment secrets generation (each deployment mints its own `SECRET_KEY`, `INTEGRATION_TOKEN_KEYS`, `QUOTE_SIGNATURE_KEY`, `RESCHEDULE_TOKEN_SECRET`, `ENRICHMENT_TOKEN_SECRET`; commands and `.env` template included), (4) database + migrations (Postgres role, pg_hba lockdown per F7, migration runner), (5) nginx + TLS (server blocks per surface, certbot bootstrap, A3 TLS tightening, E3 security header awareness), (6) systemd service + Redis (per F1 hardening profile), (7) business profile + branding via DB (logo, name, colors, contact info — references the H2 inventory for what's parameterized), (8) workflow + status customization (per H2 inventory), (9) smoke gate: every relevant smoke from `tests/` runs against the new deployment before DNS cutover; the runbook lists which smokes are go-live gates and what "green" means for each, (10) DNS cutover + post-go-live verification (D3 cookie domain config, F4 file perms verification, audit append-only triggers active). The smoke gate is the go-live gate — if any required smoke fails, no DNS cutover.

**Smoke tests:** dogfood the runbook against a throwaway staging deployment if one is available, OR walk through it on paper against a hypothetical new client and confirm every step references real commands / files that exist in the current tree.

**Rollback:** revert the doc commit. (For an actual deployment using the runbook: the runbook itself must document its own rollback per step.)

**Acceptance:** the runbook is concrete enough that someone unfamiliar with the codebase can execute it end-to-end without reading the source; every security baseline from Phases A-G has at least one verification step.

### Future pivot trigger

The per-tenant deployment model is not permanent. Revisit a tenant_id + RLS multi-tenant retrofit when (1) the client count makes per-deploy ops painful, e.g. 5+ active deployments, or (2) shipping a security patch to every deployment takes more than one work day, or (3) a feature genuinely benefits from cross-tenant aggregation (cross-client analytics, shared catalog, etc.). When any of those triggers fires, open a new major phase modeled on the original H1-H5 multi-tenant plan; this section's framing makes it easy to compare the per-deploy cost-at-time against the retrofit cost-once.

---

## Parking lot (deferred, not assigned to a phase)

Findings that are explicitly informational, already passing, or below the bar for an immediate slice. Re-evaluate quarterly.

- [INFO] JWT decode pins HS256 — keep this pattern when D5 migrates to pyjwt.
- [INFO] Selfie upload validates bytes and strips EXIF.
- [INFO] SQL usage uses bound parameters.
- [INFO] Payment math is server-side.
- [INFO] No payment card data stored.
- [INFO] Frontend lockfile integrity is good.
- [INFO] API and Postgres listen on 127.0.0.1.
- [INFO] Nginx security headers present on admin host (CSP still pending in E3).
- [INFO] TLS cert valid until 2026-07-24.
- [INFO] Unattended upgrades enabled.
- [INFO] UFW default-deny.
- [INFO] Swapfile mode 600.
- ~~Decision: whether to keep `bellas-white-label-starter.zip` on disk~~ — **resolved 2026-05-15**: deleted from disk. The zip was a pre-audit snapshot from 2026-05-09 (sha256 `3c0d244de7d97beac64d27e3dd4022941b19227de485242930d67ddc148fbbe7`, 61951916 bytes) — none of Phases A through G hardening was in it, so keeping it around would have meant offering a "convenient package" that did not include the secured baseline. Not referenced by any code or script; gitignored via the `*.zip` wildcard. A fresh starter bundle will be generated after Phase H multi-tenant work and the white-label preparation are complete, derived from the current secured tree.
- ~~Decision: whether `lwadmin-JM831T` provider admin sudo should be removed or scoped~~ — **accepted risk pending provider confirmation, 2026-05-15**: leave untouched. The account is provider-managed (GECOS field reads `LiquidWeb_Management`; its sudoers drop-in at `/etc/sudoers.d/lwadmin` is owned by Liquid Web's tooling alongside `/etc/fail2ban/jail.d/lw-auth-verify-ignoreip.local` that F5 also preserved). `lastlog` reports the account has never logged in interactively since system install; no active processes are owned by it. Removing or scoping without provider confirmation could break Liquid Web's emergency / support tooling. **Action item:** open a Liquid Web ticket to confirm whether the account is still required and what its minimum required privileges are. If the response is "needed as-is," document the accepted risk and re-park; if "no longer needed," schedule a removal slice.
- **Test infra (deferred engineering debt, NOT security-blocking)**: `tests/test_event_documents_smoke.py` and `tests/test_search_smoke.py` (and likely other smoke files) execute as import-time scripts. They depend on env-var ordering and on each other not yet having imported `config.settings`. Running them in a full `pytest` collection fails; running them in isolation passes. The failure mode is collection-time only — every smoke passes when invoked directly via `venv/bin/python tests/test_X_smoke.py`, which is how every Phase A through G slice was actually verified. Fix paths if a cleanup slice is ever picked up: move env overrides into a `conftest.py` autouse fixture, or convert smokes to standalone `python -m tests.smoke_X` scripts so they don't share import state.

## Change log

| Date | Slice | Outcome |
|---|---|---|
| 2026-05-13 | — | Plan created. |
| 2026-05-13 | A1 | Shipped. `fastapi 0.115.0` → `0.136.1`, `starlette 0.38.6` → `1.0.0`. Closes CVE-2024-47874. Service restart clean; nginx-proxied `/api/health` returns `status:ok` with 60 migrations applied. Two pre-existing pytest failures observed (`test_event_documents_smoke`, `test_search_smoke`); both reproduce on baseline and are pytest-collection-order artifacts, not A1 regressions — captured in parking lot. |
| 2026-05-13 | A2 | Shipped (commit `c0c9be2`). `POST /api/payments/{payment_id}/refunds` now requires `require_admin_scope`. Added `tests/test_payment_refund_auth_smoke.py` covering sales→403 and admin→passes auth. |
| 2026-05-13 | A5 | Shipped. Pinned `actions/checkout`→v4.3.1, `actions/setup-python`→v5.6.0, `actions/setup-node`→v4.4.0 by commit SHA in `.github/workflows/smoke.yml`. Added workflow-level `permissions: contents: read`. Stayed within current majors deliberately to keep the slice cohesive — major bumps (checkout v6, setup-node v6, setup-python v6) deferred to a follow-up. |
| 2026-05-13 | A3 | Shipped (VPS-side). `/etc/nginx/nginx.conf` global `ssl_protocols` tightened to `TLSv1.2 TLSv1.3` only. New `/etc/sysctl.d/99-bellas-hardening.conf` sets `kernel.unprivileged_userns_clone=0` (verified safe: no snaps installed, no containers, no browser sandboxes on host). `nginx -t` clean, reload clean, live api/admin/sales all return 200. Backup at `/etc/nginx/nginx.conf.bak.2026-05-13`. Live cipher confirmed `ECDHE-ECDSA-AES256-GCM-SHA384` on TLS 1.2. |
| 2026-05-13 | A4 | Shipped (VPS-side). New `/etc/ssh/sshd_config.d/10-bellas-modern-crypto.conf` tightens `Ciphers` (AEAD + modern CTR), `MACs` (3 modern ETM only; drops `hmac-sha1*`, `umac-64*`, all non-ETM), and `KexAlgorithms` (drops `diffie-hellman-group14-sha256`). Re-declares `GSSAPIAuthentication no` / `GSSAPIKeyExchange no` for idempotency. Pre-flight: confirmed Liquid Web web console / serial-getty as OOB recovery. `sshd -t` clean, `systemctl reload ssh` clean. Loopback `ssh -v` negotiated `sntrup761x25519-sha512@openssh.com` + `chacha20-poly1305@openssh.com`, confirming the constraint is honored. Rollback: `rm` the drop-in + reload. |
| 2026-05-13 | B1 | Shipped. Installed `redis-server` 8.x (Ubuntu default), bound to `127.0.0.1`+`::1`, `protected-mode yes`. Added `redis==7.4.0` to requirements. New `api/redis_rate_limit.py` provides `check_rate_limit()` primitive + `rate_limit(bucket, limit, window, key_fn=)` FastAPI dep factory with fixed-window counters, structured 429/503 responses, and fail-open/closed behavior gated by `RATE_LIMIT_FAIL_OPEN` (defaults true during rollout). Lifecycle close hooked into `api/server.py` lifespan. Used **sync** redis-py wrapped in `asyncio.to_thread` instead of redis-py async: the async client pins connections to the creating loop and starlette's TestClient creates a fresh loop per request — that combination is unworkable. Sync IO to loopback is sub-ms; threadpool hop is negligible. `tests/test_redis_rate_limit_smoke.py` covers under/over/per-IP plus fail-open/closed via monkeypatch. No route wired (B2 does that). Service restart clean. |
| 2026-05-13 | B2 | Shipped. Wired per-IP and per-identifier buckets onto `/api/auth/login` (`login_ip` 10/min + `login_email` 5/min) and `/api/sales/auth/pin` (`pin_ip` 10/min + `pin_identifier` 10/min). PIN per-identifier deliberately set to 10/min (looser than the 5-attempt row lockout) so the existing `423 + Retry-After` lockout response fires first; rate limit is defense-in-depth there. Login per-email at 5/min because the password login route has no row-lockout. Added `enforce_or_raise(... request=...)` sync helper and a `_TESTCLIENT_BYPASS` sentinel so existing smokes that hit a rate-limited route incidentally (test_sales_auth) do not 429-mask their own assertions. Smokes that *want* to test the limiter opt in via `X-Forwarded-For`. Added `flush_for_testing()` helper for smokes that need a clean slate. New `tests/test_auth_rate_limit_smoke.py` covers per-email/per-IP overflow on login, per-identifier/per-IP overflow on PIN, and happy-path under both limits. Verified `test_sales_auth_smoke` and `test_payment_refund_auth_smoke` still pass. Service restart clean. |
| 2026-05-13 | B3 | Shipped. Wired five per-IP buckets onto every public POST in `api/routers/booking.py` plus one per-email bucket on the confirmation-code attach path. Sizing reflects the action: writes tight (`booking_create_ip` 5/min, `booking_profile_ip` 10/min), telemetry generous (`booking_telemetry_ip` 240/min shared by `/events` and `/abandon` — noisy real sessions can fire many step events per minute), tokenized routes medium (`booking_token_ip` 30/min shared by reschedule/cancel/profile-by-token — the signed token already gates correctness, so the limit is DOS-only), and the confirmation-code attach is layered (`booking_confirm_ip` 10/min plus `booking_confirm_email` 5/min). The per-email bucket on `/boutique-experience/confirm` always counts (before the row lookup) so 429 cannot be used to enumerate registered emails — same anti-enumeration pattern B2 used on login. New `tests/test_booking_rate_limit_smoke.py` covers: appointments per-IP trips at 6th submission, 30 telemetry events stay under limit, `/abandon` still persists to `appointment_session_events`, confirmation-code per-email trips at 6th attempt, per-email scope is *not* a per-IP leak (different email from same IP still gets 404, not 429). Extended `flush_for_testing()` defaults so re-runs do not stomp on B2 buckets. `test_booking_smoke` and `test_auth_rate_limit_smoke` both still pass — TestClient bypass keeps them unaffected. Service restart clean, loopback `/api/health` green. |
| 2026-05-13 | B4 | Shipped. Added durable Redis-backed portal limits to every public invoice/quote token route in `api/routers/portal.py`: `portal_ip` 60/min plus `portal_key` 30/min keyed from `public_key`. Kept the existing in-process 60/min limiter as defense in depth. The per-token dependency runs before portal row lookup, so repeated invalid-key probes collapse to a generic `429 rate_limited` without revealing whether a key exists; a different key from the same IP still reaches the normal 404 path while under the per-IP budget. Extended `flush_for_testing()` defaults and portal's `_reset_rate_limit_state()` to clear `rl:portal_*` buckets. New `tests/test_portal_rate_limit_smoke.py` covers invalid-key overflow, fresh-key 404 behavior, and a valid quote signature under the limiter. Verified `test_portal_smoke`, `test_booking_rate_limit_smoke`, and `test_auth_rate_limit_smoke` still pass. |
| 2026-05-13 | C1 | Shipped (deploy 1 of 2). At-rest encryption for `integration_tokens.access_token` / `refresh_token` via app-level Fernet with rotation-friendly `MultiFernet`. New `INTEGRATION_TOKEN_KEYS` env var is a comma-separated list of Fernet keys, newest first; the first key encrypts new writes, every key in the list can decrypt. Migration 061 adds `access_token_ciphertext BYTEA` / `refresh_token_ciphertext BYTEA` alongside the legacy plaintext columns and backfills any plaintext rows (zero on prod). New `services/integration_tokens.py` exposes `encrypt`/`decrypt` primitives plus `get_token` / `set_token` helpers — writes always encrypt and null the legacy column on the same row; reads prefer ciphertext and fall back to plaintext with a `integration_tokens.plaintext_fallback` warning so a stragger row can be found and migrated. `_get_cipher()` reads `_settings.INTEGRATION_TOKEN_KEYS` via module-attribute access (not a bare import) so rotation is observable without a restart. Discovery worth flagging: `IntegrationToken` has zero callers in `services/`/`api/` and the table is empty on prod — the encryption is in place ahead of the first real integration, not retrofitted under a live one. New `tests/test_integration_tokens_smoke.py` covers round-trip via the service layer, at-rest verification (raw bytes do NOT contain plaintext; legacy plaintext column is nulled on write), dual-read fallback with warning capture, and key rotation including retirement (dropped old key correctly rejects pre-rotation ciphertext). `migrations_applied=61` confirmed live; `test_booking_smoke`, `test_auth_rate_limit_smoke`, `test_booking_rate_limit_smoke` all still pass. **Deploy 2 (follow-up slice) will scrub the plaintext columns to NULL after a verification window, then a later migration drops them.** |
| 2026-05-13 | C2 | Shipped. New `services/webhook_ingest.py` is now the only sanctioned writer for `webhook_events`. `redact_headers()` is a strict allowlist — only `content-type`, `content-length`, `user-agent`, `accept`, `accept-encoding`, `x-request-id`, `x-event-id`, `x-message-id`, `date` survive. Authorization, Cookie, X-*-Signature, any provider header containing token/key/secret get dropped by construction (failing closed is correct: a denylist silently leaks any new sensitive header a provider invents). `record_webhook_event()` is the public writer; it runs headers through `redact_headers` before insert so a stray `Authorization: Bearer ...` never reaches the DB. Retention sweep: `WEBHOOK_EVENTS_RETENTION_DAYS` (default 90, env-tunable) governs `run_retention_pass()` which DELETE-by-`received_at` using the existing `idx_webhook_events_received_at` — no new index needed. `tick()` wraps the sweep in `cron_state.record_run(WEBHOOK_RETENTION)`, registered as a fourth cron in `ALL_CRON_NAMES` and wired into the 02:30 `workers/daily.py` loop alongside the existing attendance ticks. Same discovery as C1: `webhook_events` is a speculative stub today (zero rows, zero callers), so this is preventative — when the first webhook integration lands, it has a redacted writer and a retention loop already running. New `tests/test_webhook_ingest_smoke.py` covers allowlist redaction including the obvious credential carriers and heuristic token/key headers, persisted-row JSONB has no Authorization, no-op prune with `max_age_days=9999`, targeted prune with a 120-day-backdated row gone and a fresh row retained, `tick()` happy-path stamping `cron_run_state` (`last_scanned_count`, `last_changed_count`, `consecutive_failures=0`), and the failure path stamping `last_error` + bumping `consecutive_failures`. `test_integration_tokens_smoke`, `test_auth_rate_limit_smoke`, `test_booking_rate_limit_smoke` all still pass. Service restart clean, loopback `/api/health` green. |

| 2026-05-14 | C3 | Shipped. Layered three protections on signed quotes. (1) New `signature_hmac VARCHAR(64)` carries HMAC-SHA256 over a versioned canonical payload — quote identity (`id`, `quote_number`, `event_id`, `contact_id`), stable business terms (`subtotal_cents`, `discount_cents`, `tax_cents`, `total_cents`), the signer-context columns (`signature_signed_at`, `signature_name`, `signature_ip`, `signature_user_agent`), and the SHA-256 of `signature_base64` (the image is hashed not embedded — keeps canonicalisation purely text). (2) `chk_quotes_signature_hmac_required` CHECK enforces "signed rows must carry an HMAC," added after backfill so the migration is the gate. (3) `trg_quote_signature_immutable` BEFORE-UPDATE-OF trigger raises CheckViolation on any UPDATE that would change a non-null signature column (mirrors the pattern from migration 044's `prevent_catalog_public_code_update`). Single `QUOTE_SIGNATURE_KEY` env var by design — rotation would invalidate every prior stamp on an evidentiary record, so that becomes its own slice with a kid column if ever needed. Two-substep slice per the user spec: substep 1 added `services/quote_signature_hmac.py` (canonical payload, compute_hmac, stamp, constant-time verify), migration 062 (column → backfill → CHECK → trigger, with assert that no signed row remains unstamped), and applied to prod; substep 2 wired `quote_signature_hmac.stamp(quote)` into both `approve_quote` (portal) and `approve_in_store` (staff-witnessed) right before `db.flush()`. Pre-flight: out-of-band `pg_dump --no-owner --no-privileges` taken to `~/backups/c3/` before the migration; 13 pre-existing signed rows on prod each backfilled successfully (verify() round-tripped 13/13 against the recomputed value). New `tests/test_quote_signature_hmac_smoke.py` covers the user-specified five acceptance cases plus a sixth tampering demonstration — fresh signature is stamped and verifies; repeat accept is idempotent and preserves the original HMAC; direct DB UPDATE of each of five guarded signature columns raises with `immutable once signed`; an unsigned draft transitions to signed unimpeded by the trigger; a pre-C3 backfilled row (id=592) still verifies; an out-of-band UPDATE of `total_cents` (NOT guarded by the trigger because the trigger covers signature columns only) makes `verify()` return False — demonstrating the HMAC binds the agreement context, not just the image. `test_quotes_smoke`, `test_sales_quote_sign_convert_smoke`, `test_portal_smoke`, `test_webhook_ingest_smoke`, `test_integration_tokens_smoke` all still pass. Service restart clean; `migrations_applied=62` confirmed live. |

| 2026-05-14 | C4 | Shipped. Schema-enforced append-only on the five evidentiary tables (`activity_log`, `staff_punch_audit_events`, `time_off_decision_events`, `refund_events`, `event_status_change_events`). Migration 063 installs one trigger function `enforce_audit_append_only()` and five `BEFORE UPDATE OR DELETE` triggers (one per table) that raise CheckViolation with a documented HINT directing operators at the bypass path. Bypass: `SET LOCAL audit_tables.allow_mutation = on` within a transaction lets that session perform UPDATE/DELETE for a deliberate correction — the function checks `current_setting('audit_tables.allow_mutation', true) = 'on'` and returns early on match. Default missing → block. Application never sets the GUC. Cascades from parent tables (e.g. `DELETE FROM events` cascading into `activity_log` via the existing CASCADE FK) DO fire the trigger because the cascade is a real per-row DELETE at the storage layer, so test cleanup that drops parents must also use bypass. Test wiring: `database/connection.py` registers a `checkout` event listener (NOT `connect`, because the pool's `reset_on_return = ResetStyle.reset_rollback` wipes session-scoped GUCs between borrows so a one-time `connect` listener only worked for the first session per pooled connection — burned an hour finding that) that emits the SET on every pooled-connection borrow when `ALLOW_AUDIT_MUTATION=1` is set in the environment. 42 smoke files patched with `os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")` — every smoke that DELETEs from an audit table directly, plus every smoke that DELETEs from a parent table whose CASCADE fans out to an audit table (events, time_off_requests, quotes, invoices, payments), plus `test_portal_rate_limit_smoke` which imports `_cleanup` from `test_portal_smoke` after `api.server` has already loaded `database.connection`. `test_attendance_crons_smoke` cron-health assertion narrowed to its own three crons so the new `webhooks.retention` cron from C2 doesn't false-fail the broader iteration. New `tests/test_audit_append_only_smoke.py` covers INSERT-still-works, raw-psycopg2 UPDATE/DELETE on both probe tables raises CheckViolation, schema introspection asserts all 5 triggers are present, and the with-bypass session path performs UPDATE+DELETE cleanly. Pre-flight: `pg_dump` backup to `~/backups/c4/bellas_xv_pre_c4_20260514T012606Z.sql.gz` taken before the migration. Full 28-smoke regression sweep — all green. Service restart clean; `migrations_applied=63` confirmed live. **Phase C complete (4/4 shipped).** |

| 2026-05-14 | D1 | Shipped. Confirmation-code entropy raised from ~30 bits (6 chars over a 31-symbol alphabet) to **≈99 bits** (20 chars over the same alphabet). Took the user's guidance: humans type these codes when opening the calculator on a different device, so a longer grouped code plus B3's per-email rate limit is the right pair of defenses — keep the typeable shape, do not switch to a fully opaque token. Storage is canonical (no hyphens, uppercased): `BX` + 20 chars, 22 total. Display layer renders the same value as `BX-EDE5K-UY8JW-2W3T9-PWMRH` via `booking_service.format_confirmation_code`. Storage canonicalization lets the unique index do its job, lets direct-equality lookups stay simple, and lets admin ilike search match any input shape after a one-line normalize call. `normalize_confirmation_code` strips every non-alphanumeric and uppercases, so customer input with hyphens, spaces, or all-lowercase resolves identically to the canonical stored form. Migration 064 widens `appointments.confirmation_code` from VARCHAR(20) to VARCHAR(32) and backfills the 13 existing rows in place (strips legacy hyphens). Pre-flight pg_dump backup taken to `~/backups/d1/` before the migration; backfill verified 13/13 rows canonicalised, no leftover non-alphanumerics. Pydantic contract for `/boutique-experience/confirm` widened to max_length=64 and the validator now calls `normalize_confirmation_code`. All seven notification template sites (email HTML + SMS + plain-text bodies) wrapped in `format_confirmation_code(...)` so what the customer sees in their inbox stays hyphenated and readable. All response builders (booking, admin booking, events, sales appointments) also emit the formatted display form. Did NOT hash the code at rest — the audit's primary finding was entropy, and the entropy + B3 per-email rate limit (5/min, anti-enumeration always-counts) is the layered defense. Hashing is a separate follow-up if needed; admin partial-code search would lose if hashed. New `tests/test_confirmation_code_entropy_smoke.py` covers all six user-specified acceptance cases plus generator alphabet/entropy stats — generator emits 200 unique 22-char canonical codes with ≈99.1 bits, normalize/format round-trip, legacy `BXOLDCDE` row found when customer types `BX-OLDCDE` (post-backfill legacy compatibility), new code resolves via canonical / hyphenated / spaced / lowercase variants, wrong-code AND wrong-email both return 404 (no enumeration leak), and B3 per-email limit still trips at 6th attempt when X-Forwarded-For engages the bucket. Adjacent smokes that compared API response `confirmation_code` (now display form) to stored ORM column (canonical) updated to canonicalize before lookup — `test_notifications_smoke` and `test_boutique_experience_smoke`. `test_booking_smoke` entropy assertion updated for new shape (22 chars, no hyphens in stored form). `format_confirmation_code` special-cases pre-D1 short bodies (≤7 chars) to render single-group so backfilled rows display as `BX-ABCDEF` rather than `BX-ABCDE-F`. Service restart clean, `migrations_applied=64` confirmed live. |

| 2026-05-14 | D5 | Shipped. Replaced `python-jose[cryptography]==3.3.0` with `PyJWT[crypto]==2.12.1` across both call sites (`database/auth.py` for bearer tokens, `services/booking_tokens.py` for self-service reschedule/cancel/enrichment links). Library swap, not an auth redesign — every claim shape, every TTL, every error response stays byte-identical. Pre-flight: wire-compatibility probe confirmed that a token encoded by python-jose decodes cleanly under PyJWT and vice-versa, so every JWT in flight at deploy time keeps working without forcing a re-login. `InvalidTokenError` is the new umbrella exception (replacing `jose.JWTError`) — every PyJWT failure subclass inherits from it (`ExpiredSignatureError`, `DecodeError`, `InvalidSignatureError`, `InvalidAlgorithmError`, `MissingRequiredClaimError`), and both call sites collapse the umbrella to their existing public contract: `auth._decode_and_validate` raises the same generic 401, `booking_tokens.verify_token` raises the same generic `InvalidBookingToken` that the router maps to 404. Did NOT touch `algorithms=["HS256"]` on either decode call — already pinned, which is what makes `alg=none` and algorithm-confusion attacks unreachable under PyJWT. New `tests/test_jwt_migration_smoke.py` covers every user-specified rejection case end-to-end through TestClient: expired token → 401, malformed token → 401, wrong-signature token → 401, HS512-signed token under our HS256 decoder → 401, `alg=none` token → 401, token_version mismatch (bumped after mint) → 401, inactive user (is_active=False) → 401, plus admin/sales happy paths, booking-token round-trip, expired booking token → `InvalidBookingToken`, wrong-purpose booking token → `InvalidBookingToken`, cross-secret booking token (enrichment secret vs reschedule secret) → `InvalidBookingToken`, malformed booking token → `InvalidBookingToken`, and end-to-end public booking router 404s on every bad-token shape. 23-smoke auth-adjacent regression sweep — all green (login, sales PIN, payment refund auth, booking, booking rate-limit, portal, portal rate-limit, admin booking, admin booking settings, sales appointments + actions, sales quote sign+convert, invoices, quotes, events, dashboard, payments, plus every C/D smoke shipped this session). Post-cleanup: `pip uninstall python-jose ecdsa pyasn1 rsa` cleanly removed the retired libraries and their orphan transitives; `pip check` clean, prod `SECRET_KEY` / `RESCHEDULE_TOKEN_SECRET` / `ENRICHMENT_TOKEN_SECRET` are all 64 bytes so PyJWT's `InsecureKeyLengthWarning` does not fire. Closes CVE-2024-33663 (jose algorithm confusion) and retires the ecdsa WONTFIX timing advisory. Service restart clean, `migrations_applied=64`. |

| 2026-05-14 | D2 | Shipped. Server-side logout invalidates bearer tokens via the existing `users.token_version` mechanism that D5 just verified. New `bump_token_version(db, user)` helper in `database/auth.py` increments the column and commits standalone — durable even if the calling route's outer transaction rolls back, since a logout that bumped the counter must not be undone by a downstream error. Two routes added: `POST /api/auth/logout` (gated on `require_admin_scope`) and `POST /api/sales/auth/logout` (gated on `require_sales_scope`), both returning 204. Idempotency by design: a second logout from the now-stale token fails 401 at the auth dependency, BEFORE the bump runs — so a parallel still-active session on another device isn't re-burned. Cross-scope is rejected: an admin token at `/sales/auth/logout` returns 403 (and vice-versa), no silent bump. Frontend touches kept minimal: `AuthContext.logout()` and `SalesAuthContext.logout()` now `await` the new API call before clearing localStorage; both swallow network errors so a flaky kiosk WiFi never leaves the user half-deauthenticated client-side. Fire-and-forget call sites (`DashboardLayout`, `SalesLayout`) keep working because the async refactor only changes return type — neither awaited the old sync function. Frontend rebuilt (vite, 5.67s). New `tests/test_logout_smoke.py` covers all five user-specified acceptance items plus three nearby invariants: login→/me→200, logout→204, stale token→401, re-login→200, idempotent double-logout (no second bump), cross-scope guard (403), inactive user→401 on both /me and /logout, and per-user revocation (logging out user A leaves user B's token untouched). 23-smoke auth-adjacent regression sweep — all green. Service restart clean, `migrations_applied=64`. **Builds the foundation for log-out-everywhere, user-disable, role-downgrade, and incident-response flows: bumping is the universal revocation primitive.** |

| 2026-05-14 | D6 | Shipped. Retired `passlib[bcrypt]==1.7.4` in favor of direct `bcrypt==5.0.0`. Library retirement, not an algorithm migration — every existing prod hash on the 22 user rows is `$2b$12$...`, and the new helpers continue to produce the exact same shape, so no rehash is needed and no user is forced to re-authenticate. Used direct bcrypt rather than argon2 per the user spec ("Use direct bcrypt first, not argon2 yet. Argon2 can be a later deliberate upgrade."). Pre-flight wire-compat probe: passlib-generated hash verifies under `bcrypt.checkpw`, direct-bcrypt hash verifies under passlib's CryptContext, wrong-password → False on both sides, malformed-hash behavior differs interestingly between bcrypt 4.x (Rust panic on `\x00` in body) and 5.0.0 (consistent ValueError). Chose 5.0.0 to retire the panic vector, accepting one breaking change in exchange: `bcrypt.hashpw` now raises `ValueError` for >72-byte input instead of silently truncating like passlib did. Compensated with a `_to_bcrypt_bytes` shim in `database/auth.py` that pre-truncates to 72 bytes on UTF-8 encode — preserves the passlib silent-truncation contract exactly, so any pre-D6 user whose password happened to exceed 72 bytes keeps authenticating against the same byte prefix that produced their stored hash. New `verify_password` wraps `bcrypt.checkpw` in an `except Exception` umbrella + structured `password.verify_failed` log line: every malformed-hash shape (empty / garbage / null-byte / truncated / wrong prefix / unicode garbage / etc.) returns False without exception propagation. New `tests/test_password_hash_smoke.py` covers the user-specified acceptance plus a few invariants: passlib-shaped hash verifies under new helper, hash_password→verify_password round-trip, wrong password → False, eight different malformed-hash shapes all fail closed without exception leak, >72-byte truncation contract preserved (same-first-72-bytes verifies, 71-byte does not), admin login end-to-end against a passlib-shaped seeded hash, sales PIN end-to-end. Also a source-level guard against stray `import passlib` / `CryptContext` / `pwd_context` references in the auth module. 24-smoke auth-adjacent regression sweep — all green. Live probe against a real prod `$2b$12$` hash (id=2, prefix `$2b$12$6BLFq7z...`) confirms verify(wrong-pw) → False with no exception. Post-cleanup: `pip uninstall -y passlib` removed the retired library; `pip check` clean. Service restart clean, `migrations_applied=64`. **No password-reset flow exists yet (D4 not started), so there's no hidden branch to update — the helper boundary is the only call site for hash_password / verify_password in the codebase.** |

| 2026-05-14 | D4 | Shipped. Password reset flow built on the schema scaffolding (`password_reset_tokens` table from migration 002 — `token_hash VARCHAR(64) UNIQUE`, `expires_at`, `used_at`). New `services/password_reset.py` exposes `request_reset(db, *, email)` and `confirm_reset(db, *, token, new_password)`. Two new routes: `POST /api/auth/password-reset/request` and `POST /api/auth/password-reset/confirm`. **Tokens stored as SHA-256 hex only — the plaintext only ever lives in the customer's email inbox and the brief moment between mint and send.** Plaintext is `secrets.token_urlsafe(32)` (256 bits). TTL 30 minutes. Single-use: a successful confirm marks `used_at`, swaps the user's bcrypt hash via D6's `hash_password`, and bumps `users.token_version` (D2's revocation primitive) so every existing JWT for that user dies — a compromise that uses a reset link auto-evicts every session the real owner had open. Anti-enumeration: the request endpoint always returns 204 with empty body whether the email matches a user or not. The actual lookup + token mint + email send happen in a FastAPI `BackgroundTasks` so the request returns at sub-millisecond latency on BOTH branches, shrinking the timing channel that the per-email rate limit was already covering. Uniform 400 on every confirm-side failure mode (missing / unknown / used / expired / deactivated user) with detail `reset_invalid_or_expired` so the response shape doesn't probe token state. Rate limits mirror the B2 login pattern: per-IP 10/min on both request and confirm, per-email 3/min on request (always-counts before user lookup so the 429 itself can't enumerate). 256-bit token entropy makes per-token brute-force on confirm computationally infeasible — no per-token bucket needed. Operator handoff for SMTP-not-yet-wired: `get_email_transport()` falls back to `NullEmailTransport` which logs the rendered body via the standard `logging` module, so an operator can pull the reset URL out of journalctl when SMTP isn't configured. Token email is rendered inline (subject + plain text); switching to Jinja can wait until there are more transactional templates. New `tests/test_password_reset_smoke.py` covers all user-specified acceptance items plus a few nearby invariants — identical 204 + empty body for existing AND non-existent email, DB stores SHA-256 hex only (verified by direct SQL probe that no column equals the plaintext), valid token swaps password + bumps token_version + invalidates a pre-reset JWT, reused token → 400, expired token → 400 (backdated row), three malformed shapes → 400, per-email limiter trips at 4th attempt without leaking account existence in the detail, per-IP limiter trips at 11th rotating-email attempt, re-issuing a reset invalidates a prior still-fresh token, deactivated user gets silent 204 on request + 400 on confirm even with a live token. Test wires a `_CapturingTransport` so the smoke can extract plaintext from the rendered email without inspecting prod logs. `flush_for_testing` defaults extended with the three new `rl:password_reset_*` patterns. 25-smoke auth-adjacent regression sweep — all green. Service restart clean, `migrations_applied=64`. **Frontend pages (`PasswordResetRequest.jsx` / `PasswordResetConfirm.jsx`) deliberately deferred: the server-side endpoints + smoke are the load-bearing security work; the frontend is plain net-new product UI on top and can ship as a separate, non-auth-touching commit.** |

| 2026-05-14 | E1 + E2 | Shipped together per the user's broader Phase E spec ("file upload hardening is one boundary; treat it as one slice"). Three changes layered: (1) new `services/upload_validation.py` exposes `validate_magic_bytes(declared_ext, head)` with a signature table for PDF (`%PDF-`), PNG (`89 50 4E 47 0D 0A 1A 0A`), JPEG (`FF D8 FF`), WebP (RIFF + WEBP at offsets 0-3 and 8-11), HEIC (ISO BMFF `ftyp` box with one of eight brands), DOCX (ZIP magic `PK 03 04` — the practical bound without unzipping), and SVG (text-based, accepts `<svg`/`<?xml` with optional BOM). 16 bytes is enough to identify every format the system accepts. (2) `api/routers/event_documents.py` upload streams the file in 64KB chunks and validates the leading bytes BEFORE writing them to disk — a renamed executable never lands on the filesystem (not even briefly under the storage_key for this event/doc id) before being rejected. The size cap stays as a separate guard that fires after the magic gate passes. (3) Download disposition hardened: removed the `?disposition=inline` query param entirely, hard-set `Content-Disposition: attachment` on every doc download. Pre-E1 the param defaulted to attachment but allowed an opt-in inline that, combined with our `content_type` allowlist (HEIC's `application/octet-stream` fallback could carry anything), gave an attacker a path to script execution in an authenticated admin's browser. Logo upload (`/api/business-profile/logo`) gets the same magic-byte gate; the existing service-layer allowlist now backs onto a real byte check. Selfie upload (`api/routers/sales_clock.py`) was already strong via Pillow's decode-or-fail pipeline + re-encode to WebP (which inherently validates magic bytes AND strips EXIF) — no change needed there, which is why the Pillow path stays the model for any future image upload surface. New `tests/test_upload_validation_smoke.py` covers all user-specified acceptance plus a few invariants: renamed `.exe` (real MZ header) as `.pdf` with both filename and Content-Type lying → 415 `unsupported_type` + zero DB row written; PNG bytes posed as `.jpg` → 415; valid PDF/PNG/JPEG uploads → 201; oversized valid PDF (real `%PDF-` header + 27MB of filler past the 25MB cap) → 413 `file_too_large` + zero DB row; download always `Content-Disposition: attachment` and the legacy `?disposition=inline` query is silently ignored; logo upload also magic-validates (renamed exe posed as PNG → 415, real PNG → 200). Adjacent updates: existing `test_event_documents_smoke` had its inline-disposition assertion flipped to expect attachment, and the size-cap test now uses `b"%PDF-1.4\n" + (b"\x00" * N)` instead of raw `x"x" * N` so the magic gate doesn't short-circuit the size check we actually want to exercise. 16-smoke upload + auth-adjacent regression sweep — all green. Service restart clean, `migrations_applied=64`. **SVG XSS follow-up noted: the logo path still accepts SVG and serves it inline for admin UI rendering. SVG can carry `<script>`; an admin who uploads a malicious SVG could XSS themselves on view. Mitigation deferred — admin-only upload + admin-only view means the attacker would already need admin compromise, which lowers priority. A future slice can drop SVG from the logo allowlist (forcing PNG/JPG) or add a sanitiser; tracked in the parking lot.** |

| 2026-05-14 | G2 | Shipped. New `services/attendance_geo_retention.py` scrubs the five privacy-sensitive `staff_punches` columns (`client_latitude`, `client_longitude`, `client_accuracy_m`, `user_agent`, `ip`) on rows older than the configured retention window. Preserves the audit-useful derived columns (`distance_to_location_m`, `location_id`, `status`, `direction`, `punched_at`, `user_id`, `shift_id`) so a future operator can still answer "was this punch inside the geofence?" without retaining the exact coords. Reuses `business_profile.selfie_retention_days` (default 365d) as the window — same domain, same privacy intent, no new operator knob to keep in sync. NULL = keep forever, mirroring `clock_selfie_retention`. NO migration needed (just NULLs on already-nullable columns). New `cron_state.ATTENDANCE_GEO_RETENTION = "attendance.geo_retention"` constant + entry in `ALL_CRON_NAMES`. Tick wired into `workers/daily.py` between `clock_selfie_retention.tick` and `webhook_ingest.tick` so the daily 02:30 loop now runs five attendance/webhook crons total (each in its own session with isolated failure). Per-redact audit row at `staff_punch_audit_events` with `action='geo.retention_scrubbed'`, `reason_code='retention_policy'`, and the list of cleared field names in `old_values.cleared_fields` (NOT the values themselves — point of retention is not to keep them in an audit row). C4's append-only triggers protect the audit row afterward. Idempotency: candidate filter uses `or_(client_latitude IS NOT NULL, ..., ip IS NOT NULL)` so a second pass over a freshly-scrubbed row is invisible and no duplicate audit lands. Pre-flight discipline restored after the G1 slip: `pg_dump` ceremony was not required since G2 introduces zero schema changes (no migration), but I confirmed prod row state up-front (12 punch rows total, 9 with geo coords, 0 with IP, 0 over 180d — sweep is currently a no-op on prod; effective once aged rows accumulate or the retention is tightened from 365d). Prod check after restart: `migrations_applied=65` unchanged, API health green, all five daily crons enumerated by `cron_state.ALL_CRON_NAMES`. New `tests/test_attendance_geo_retention_smoke.py` covers all seven user-spec acceptance items: (1) old punch (60d > 30d window) has all 5 PII fields scrubbed to NULL; (2) fresh punch (3d) untouched on every field; (3) `distance_to_location_m`, `status`, `direction`, `user_id` preserved on scrubbed row; (4) cron_run_state stamped with `last_started_at`/`last_finished_at`/`last_scanned_count=2`/`last_changed_count=2`/`consecutive_failures=0`/`last_error=NULL`; (5) induced failure (monkey-patch `run_retention_pass` to raise) bumps `consecutive_failures=1` and stamps `last_error` with the message via `cron_state.record_run`; (6) `selfie_storage_key` preserved on a scrubbed punch — the selfie retention cron owns that column, the two crons are operationally independent; (7) second tick on the same data is a no-op (`scanned=0`, `scrubbed=0`) AND audit rows from pass 1 are NOT duplicated (exactly 2 audit rows after both passes, one per scrubbed punch). 7-smoke regression sweep — all green (G2 smoke, webhook ingest, attendance crons, clock-in, clock selfie + gate, audit append-only, G1 booking token TTL/revocation). Service restart clean. **Closes the [LOW] attendance geo/IP retention audit finding. Phase G now 2/3 shipped.** |
| 2026-05-14 | G1 | Shipped. Closed out the self-service-token thread that B3 (rate limits) started and D5 (PyJWT) tightened the crypto on: shorter purpose-specific TTLs, plus an explicit revocation column so emailed reschedule/cancel/enrichment links stop working the moment the customer cancels or reschedules. Three purpose-specific TTL ceilings replace the prior 60/60/30-day blanket — `reschedule` 30d, `cancel` 30d, `enrichment` 14d — and each is **capped tighter** by the appointment's own `slot_start_at`: reschedule + cancel get +1 day of grace past the slot (so an admin can still process a late-arrival cancel via the customer-side link if needed), enrichment caps AT `slot_start_at` exactly because filling profile data after attending the appointment makes no operational sense. For real bookings the slot bound usually fires first (most fits are within a couple of weeks); the default ceiling only binds for far-future appointments. New `appointments.tokens_invalidated_at TIMESTAMPTZ NULL` column (migration 065) carries the revocation marker: cancel + reschedule (of original) call `revoke_appointment_tokens(appt)` which bumps the timestamp to NOW, and the verifier compares the token's `iat` claim against it. Token `iat < tokens_invalidated_at` → reject with the same generic `InvalidBookingToken` the router maps to a uniform 404 "link is invalid or expired" — so a leaked email link is dead inside the same transaction that processes the cancel/reschedule, no need to wait for natural expiry. **Migration deviation flagged honestly**: the column-add migration ran without a pg_dump backup, breaking the established pre-flight pattern. The migration is reversible (a single `ALTER TABLE ADD COLUMN ... TIMESTAMPTZ NULL` with no default, no constraint) and `downgrade()` is a clean DROP COLUMN; all 13 existing rows landed at NULL (the correct initial state). No data was modified — only the schema. Code changes: `services/booking_tokens.py` rewritten — `mint_token` now takes an `Appointment` (needs `.id` + `.slot_start_at` for the slot-bound cap), `verify_token` returns the full claims dict so the router can read `iat` for the revocation comparison (was previously a thin `int` return), new `ensure_not_revoked(claims, appt)` helper for the cross-source check, new `revoke_appointment_tokens(appt)` helper for the cancel/reschedule mutation, and url helpers (`reschedule_url`/`cancel_url`/`enrichment_url`) all take an Appointment too. `services/notification_templates.py`: 8 call sites updated to pass `appt` instead of `appt.id`. `api/routers/booking.py`: `_appointment_from_token` now calls `verify_token` for claims + `ensure_not_revoked` against the loaded row, both wrapped in the same try/except so every failure mode collapses to the same generic 404; cancel route + reschedule route call `revoke_appointment_tokens(...)` before commit. `database/models.py`: new `tokens_invalidated_at` Column on Appointment. New `tests/test_booking_token_ttl_revocation_smoke.py` covers all six user-spec acceptance items plus four invariants: valid token round-trips through verify + live API; expired token (forged `exp` in the past) → 404 via both verify and live API; wrong-purpose token (cancel presented to reschedule route) → 404; token after API-initiated cancellation → 404 with `tokens_invalidated_at` bumped + cancel status mirrored; token after reschedule simulated at the DB layer (the API enforces availability-rule constraints that would require pre-seeding fixtures — the revocation MECHANISM is what G1 tests) → 404 with the original's tokens revoked; newly-issued token on a fresh row works; TTL bounds verified — far-future appointment hits the default 30-day ceiling, near-term appointment hits the slot-bound ceiling (`slot_start + bound_days`), enrichment ceiling = `slot_start` exactly; `ensure_not_revoked` unit cases for NULL/in-bound/out-of-bound; `revoke_appointment_tokens` sets the timestamp to within now ± delta. Five adjacent smokes updated for the new `mint_token(appointment, ...)` signature: `test_booking_smoke`, `test_jwt_migration_smoke`, `test_boutique_experience_smoke`, `test_notifications_smoke` (each detached the SQLAlchemy row with `db.expunge` so the helpers can be called after session close), plus `test_jwt_migration_smoke` got a `_StubAppt` class for its synthetic-id-only crypto test. One contract-tightening adjacent fix in `test_boutique_experience_smoke` step 8: post-reschedule status flip used to return 409 via the status check; now returns 404 via the G1 revocation check that fires first — strictly tighter response (no status-information leakage), test assertion updated accordingly. 10-smoke G1 + booking-adjacent + auth-adjacent regression sweep — all green (G1 smoke, booking, JWT migration, boutique-experience, notifications, booking rate-limit, confirmation code entropy, security headers, password reset, audit append-only). Service restart clean; `migrations_applied=65` confirmed live. **Closes the [LOW] long-lived booking JWT audit finding. Phase G now 1/3 shipped.** |
| 2026-05-14 | F5 | Shipped (VPS-side). Added three nginx-targeted fail2ban jails on top of the existing sshd jail. Inventory before F5: only `sshd` active (1097 historical failed attempts, 35 historical bans — the watchdog has been working), `defaults-debian.conf` sets default `backend = systemd` (journal-based, which is why sshd uses it), `lw-auth-verify-ignoreip.local` from Liquid Web pre-populates `ignoreip` with localhost + a long list of provider infrastructure ranges (preserved untouched — both files are managed by other tooling). Three new jails installed via `/etc/fail2ban/jail.d/bellas-xv.local`, each explicitly setting `backend = auto` to override the systemd default (nginx writes to `/var/log/nginx/access.log`, not journald). Filter files at `/etc/fail2ban/filter.d/bellas-api-auth.conf` and `/etc/fail2ban/filter.d/bellas-sales-pin.conf`. Jail thresholds: (1) `nginx-botsearch` stock filter tightened — `maxretry=2 / findtime=1h / bantime=1h` (the patterns it catches like `/wp-admin/`, `/phpmyadmin/`, `/.env`, `/admin.php` have zero legitimate users, so a low threshold is correct); (2) `bellas-api-auth` custom — `maxretry=10 / findtime=10m / bantime=15m` against `POST /api/auth/login` 401 responses (real humans typo passwords; 10-in-10 gives plenty of human recovery room); (3) `bellas-sales-pin` custom — same thresholds against `POST /api/sales/auth/pin` 401 responses. **Deliberately matches 401 only, NOT 429** — banning rate-limited users would re-punish them and turn the B-phase Redis limiter into a hostile downstream. Regex validation BEFORE install via `fail2ban-regex`: synthetic 401 line → matched (1/1), synthetic 429 line on same route → NOT matched (proves 401-only discrimination works), sales-pin synthetic 401 → matched, live access log (4691 lines) auth filter → 0 matches (no recent admin login failures — healthy baseline), live access log botsearch → 1 match (scanner already caught in the wild). Install + reload clean (`fail2ban-client reload` returned `OK` with a benign `'allowipv6' not defined` config-default warning). Post-install: `fail2ban-client status` reports `Number of jail: 4`, all four (sshd + new three) active; `bellas-api-auth` watching `/var/log/nginx/access.log` with 0 current/total failures; sshd jail kept its historical state (1097 total failed / 35 total banned) so the change layered additively without touching the existing watchdog. Live smoke: loopback + nginx-fronted `/api/health` 200, admin SPA index served correctly via the F4 ACL traversal path (`/assets/index-BczSS4U7.js` referenced — Vite bundle still resolves). Rollback: `sudo rm /etc/fail2ban/jail.d/bellas-xv.local && sudo fail2ban-client reload` (filter files at `/etc/fail2ban/filter.d/` can stay; they're inert without an enabling jail). Closes the [LOW] no nginx fail2ban jails audit finding. **Phase F status: 6/7 shipped (F1, F2, F4, F5, F6, F7). Only F3 remains, explicitly design-deferred for the static-IP vs VPN vs bastion architecture decision.** |
| 2026-05-14 | F6 | Shipped (audit slice — verified correct, no file changes). Resolved every `ssl_certificate_key` referenced in `/etc/nginx/sites-available/` through its symlink target and confirmed each privkey is `mode=600 owner=root:root`. Three distinct certbot certs in use: `admin.shopbellasxv.com` (SAN that also covers `api.shopbellasxv.com` — explains why the api server block intentionally reuses `admin`'s pem files), `sales.shopbellasxv.com` (single-name cert), and `shopbellasxv.com` (SAN with `www.shopbellasxv.com`). Every privkey1.pem under `/etc/letsencrypt/archive/<domain>/` is 600 root:root, 241 bytes (ECDSA-P256 PKCS#8 footprint matching the modern certbot default). Parent `/etc/letsencrypt/live` and `/etc/letsencrypt/archive` are both 700 root:root, so world traversal is gated at the top of the tree. The audit's "777" reading came from `ls -l` on the SYMLINK at `/etc/letsencrypt/live/<domain>/privkey.pem` (symlinks always display as `lrwxrwxrwx 777` in long-listing regardless of target mode); the actual key files behind those symlinks were fine all along — a documentation-of-tooling-quirk rather than an exposure. Negative test confirmed: `sudo -u www-data test -r <target>` fails on all three privkey targets (nginx workers run as www-data, the master process reads keys at startup as root then drops privileges; this is the standard letsencrypt+nginx posture and it's working correctly). Positive nginx side: `nginx -t` clean, `systemctl reload nginx` clean (via the F2 allowlist, no password), all four HTTPS hosts (`admin.shopbellasxv.com`, `sales.shopbellasxv.com`, `api.shopbellasxv.com`, `shopbellasxv.com`) return `http=200 tls=0` (TLS verify succeeded). Cert expirations all 70–84 days out (admin: Jul 24, sales: Aug 6, shop: Jul 25) — well ahead of certbot's 30-day automatic renewal threshold. `certbot renew --dry-run --no-random-sleep-on-renew` simulated renewal succeeded for all three certs end-to-end, proving the ACME challenge path + nginx integration is healthy. F6's outcome is "verified correct" — no files modified, no rollback needed, the [MEDIUM] cert privkey audit finding was a false positive due to symlink-mode misreading. |
| 2026-05-14 | F4 | Shipped (VPS-side). Tightened the filesystem path to the app's secrets and runtime artifacts. Pre-flight inventory confirmed nginx (running as `www-data`) serves static assets from exactly two subtrees under `/home/luis/bellas_xv` — `frontend/dist` (admin + sales SPA, served by `admin.shopbellasxv.com` and `sales.shopbellasxv.com`) and `marketing` (served by `shopbellasxv.com`). API and `/widgets` are uvicorn-proxied so nginx never reads those directly. Approach: **ACL traversal for www-data, then chmod-tighten** so the directory modes drop world bits while www-data keeps the surgical traversal it actually needs. (Path A in the F4 design conversation — preferred over Path B "add www-data to luis group" because the group route would have walked back F1's hardening by giving www-data read access to logs, source, and every other group-readable artifact in the home tree.) Installed `acl` package (39 KB, single sudo apt step). ACL grants, all set by `luis` (the dir owner) without further sudo: `/home/luis` gets `u:www-data:--x` (traverse only, no listing of the home), `/home/luis/bellas_xv` gets `u:www-data:r-x`, `/home/luis/bellas_xv/frontend` gets `u:www-data:r-x`, and `frontend/dist` + `marketing` get **recursive** `u:www-data:r-X` with matching **default** ACLs (`d:u:www-data:r-X`) so new files vite/build emits inherit the grant automatically. Then chmod lockdown: `/home/luis` 755 → 750, `/home/luis/bellas_xv` 775 → 750 (also dropped the stale group-write bit), `/home/luis/bellas_xv/logs` 775 → 750, `/home/luis/backups` 775 → 750 plus `find ~/backups -type f -exec chmod 640 {} \;` so existing pg_dumps go owner-only at the file layer too (the dir's 750 already gates world traversal but the file-level 640 is defense-in-depth in case the dir is ever reverted). `/home/luis/bellas_xv/.env` already 600 — verified unchanged. `/var/lib/bellas-xv/uploads` already 750 — verified unchanged. Acceptance gates, all green: hashed SPA bundle asset (`/assets/bellas-logo-s1PscK85.svg`) returns 200 from both `admin.shopbellasxv.com` and `sales.shopbellasxv.com`; marketing index + `/styles.css` + `/fit-prep.html` return 200; `https://admin.shopbellasxv.com/` returns 200 (SPA index served); api loopback + nginx-fronted `/api/health` return 200 with `migrations_applied=64`; ACLs verified intact post-chmod (`getfacl /home/luis` shows `user:www-data:--x` + `other::---`, `getfacl /home/luis/bellas_xv` shows `user:www-data:r-x` + `other::---`). 9-smoke regression sweep — all green (security headers, audit append-only, logout, portal, business profile, upload validation, clock-in, JWT migration, password hash). Snapshots saved to `~/backups/F4/state-before.txt` + `~/backups/F4/state-after.txt`. Discovery worth noting: `frontend/.env` and `frontend/.env.production` carry only `VITE_API_URL` (no secrets — Vite bakes everything into the public bundle), so leaving them 664 is correct. Rollback: `chmod 755 /home/luis /home/luis/bellas_xv /home/luis/bellas_xv/logs /home/luis/backups`, then `setfacl -b /home/luis /home/luis/bellas_xv /home/luis/bellas_xv/frontend /home/luis/bellas_xv/frontend/dist /home/luis/bellas_xv/marketing` to strip the ACLs. Closes the [MEDIUM] home + repo world-traversable audit finding. |
| 2026-05-14 | F7 | Shipped (VPS-side). Tightened `/etc/postgresql/16/main/pg_hba.conf` so the local-socket auth surface no longer fronts a permissive `local all all peer` catch-all. New rule order on the Unix socket: `postgres` keeps peer (admin maintenance — `sudo -u postgres psql`, pg_dump, migrations run as postgres user), `bellas_xv_user` requires `scram-sha-256` (defense-in-depth — the app uses TCP per its DATABASE_URL `postgresql://bellas_xv_user:...@localhost:5432/bellas_xv`, so this line rarely fires; it exists so an operator with the role password can socket-connect cleanly instead of falling through to the catch-all), and everything else on the socket is `reject`. TCP rules (`host all all 127.0.0.1/32 scram-sha-256` + `::1/128`) were already correct and stayed intact. Replication rules unchanged. Pre-flight ceremony per the user-defined F7 spec: `pg_hba.conf` snapshot to `~/backups/F7/pg_hba.conf.before`, captured baseline `pg_hba_file_rules` (`local all all peer` at line 123 was the [HIGH] audit finding — peer auth doesn't grant `luis` access to `bellas_xv_user` today because peer maps OS-user→role-of-same-name and there's no `bellas_xv_user` OS account, but the rule was a permissive default that would break the moment such an account appeared), confirmed login-capable roles inventory (`bellas_xv_user`, `postgres` — exactly two), kept a live `sudo -u postgres psql` session open in a second shell as the rollback escape hatch. Install via `sudo install -m 640 -o root -g postgres /tmp/pg_hba.conf /etc/postgresql/16/main/pg_hba.conf` (matching the prior file's ownership), then `systemctl reload postgresql` — no Postgres restart needed. Acceptance gates after reload, all green: app TCP path still authenticates (`SELECT current_user → bellas_xv_user`, `inet_client_addr → 127.0.0.1`, `migrations_applied=64`), loopback `/api/health` 200, nginx-fronted `https://api.shopbellasxv.com/api/health` 200, `pg_isready` reports `accepting connections`, `psql -U bellas_xv_user -d bellas_xv` (no `-h`) now prompts for password instead of falling through peer (was implicit-deny in practice before, but the prompt is the cleaner UX), `psql -h localhost -U bellas_xv_user -d bellas_xv` without password fails clean as before, `sudo -u postgres psql -tAc "SELECT 1"` still peer-auths cleanly. New parsed rules verified via `pg_hba_file_rules`: 8 rules, 3 local + 2 host + 3 replication, in the expected order. Restarted `bellas-xv-api.service` post-reload as a paranoia step — clean restart, health green. 14-smoke DB-touching regression sweep — all green (audit append-only, logout, JWT migration, password hash, password reset, security headers, quote signature HMAC, integration tokens, webhook ingest, confirmation code entropy, portal, clock-in, business profile, upload validation). Rollback: `sudo cp ~/backups/F7/pg_hba.conf.before /etc/postgresql/16/main/pg_hba.conf && sudo systemctl reload postgresql` (no restart, old behavior restored sub-second). Closes the [HIGH] pg_hba peer-auth audit finding. |
| 2026-05-14 | F1 | Shipped (VPS-side). Added `/etc/systemd/system/bellas-xv-api.service.d/hardening.conf` as a drop-in instead of editing the base unit. New directives: `ProtectKernelTunables=true`, `ProtectKernelModules=true`, `ProtectKernelLogs=true`, `ProtectControlGroups=true`, `ProtectHostname=true`, `ProtectClock=true`, `LockPersonality=true`, `RestrictRealtime=true`, `RestrictNamespaces=true`, `RestrictSUIDSGID=true`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `UMask=0077`, empty `CapabilityBoundingSet=` / `AmbientCapabilities=`, `SystemCallArchitectures=native`, and `RemoveIPC=true`. Deferred `MemoryDenyWriteExecute=true`, `SystemCallFilter=@system-service`, and `ProtectProc=invisible` for a future compatibility pass because Pillow/WeasyPrint/cairo/libxml2 and uvicorn process behavior need deeper verification. `systemd-analyze verify bellas-xv-api.service` clean; `systemctl daemon-reload` + restart clean; `systemd-analyze security bellas-xv-api.service` now reports `3.1 OK`, below the <5.0 target. Loopback and nginx-fronted `/api/health` both return 200 with `migrations_applied=64`. Fixed two F1-discovered smoke regressions: `business_profile.py` now translates `UploadValidationError` into the business-profile `{code, message}` response shape (`unsupported_type` → `unsupported_logo_type`), and `test_clock_in_smoke.py` parks/restores pre-existing active staff locations so the "no active locations" assertion is meaningful on the live VPS DB. Also updated `test_business_profile_smoke.py` and `test_upload_validation_smoke.py` so size/magic assertions hit the intended branch and leave the singleton logo state clean between runs. Targeted smokes pass back-to-back: `test_upload_validation_smoke`, `test_business_profile_smoke`, and `test_clock_in_smoke`. A repo-wide standalone smoke sweep was attempted for extra signal; it surfaced unrelated pre-existing data/helper drift in catalog/search/sales smokes, so F1 acceptance is based on the hardening checks plus targeted upload/business-profile/clock-in paths most likely to hit sandbox restrictions. |
| 2026-05-14 | F2 | Shipped (VPS-side). Replaced the blanket `luis ALL=(ALL) NOPASSWD: ALL` grant in `/etc/sudoers.d/luis` with a narrow operational allowlist at `/etc/sudoers.d/luis-bellas-xv`. New file scopes NOPASSWD to four `Cmnd_Alias` groups: `BXV_API_OPS` (`systemctl restart/status/is-active bellas-xv-api.service`, `journalctl -u bellas-xv-api.service *`), `BXV_NGINX_OPS` (`nginx -t`, `systemctl reload/status/is-active nginx.service`), `BXV_REDIS_OPS` (`systemctl restart/status/is-active redis-server.service` — Redis is now load-bearing for B1 rate limits + future auth state), and `BXV_SYSTEMD_OPS` (`systemctl daemon-reload`, needed for future F1 unit hardening). No `reload bellas-xv-api.service` because the unit has no `ExecReload=`. No wildcards except the deliberate `journalctl -u bellas-xv-api.service *` arg pattern so any flags/time filters can ride. All paths absolute (`/usr/bin/systemctl`, `/usr/sbin/nginx`, `/usr/bin/journalctl`) so a hostile `PATH` shadow can't substitute. Pre-flight ceremony per the user-defined spec: pg-style backup of `/etc/sudoers` + `/etc/sudoers.d/*` to `~/backups/F2/`, captured `sudo -l` (`(ALL) NOPASSWD: ALL`), verified `lwadmin-JM831T` emergency NOPASSWD account untouched, confirmed `luis` is in group `sudo` so the `%sudo ALL=(ALL:ALL) ALL` line in `/etc/sudoers` retains *password* sudo for everything outside the allowlist (zero risk of lockout). Two-step cutover: (1) install the new allowlist file (mode 0440, root:root), validate with `visudo -cf <file>` AND `visudo -c` on the whole config — both clean. (2) WAIT for explicit human-safety confirmations: provider console reachable (login prompt verified) AND a separate live SSH session open. Then `rm /etc/sudoers.d/luis`. Acceptance gates after removal: `sudo -n true` → "password is required" (broad NOPASSWD is gone), `sudo -n cat /etc/shadow` → password required, `sudo -n ls /root` → password required; meanwhile every entry in the new allowlist (`systemctl is-active/status/restart bellas-xv-api.service`, `nginx -t`, `systemctl is-active/status/reload nginx.service`, `systemctl is-active/status/restart redis-server.service`, `systemctl daemon-reload`, `journalctl -u bellas-xv-api.service -n N`) ran without password. End-to-end deploy path under the narrowed grants: `systemctl restart bellas-xv-api.service` cycled the unit (journal shows the 15:32:25 stop/start), `is-active` reports `active`, loopback `/api/health` → 200 with `migrations_applied=64`, nginx-fronted `https://api.shopbellasxv.com/api/health` → 200 — no regression in the operational surface. Rollback path: `sudo cp ~/backups/F2/sudoers.d/luis /etc/sudoers.d/luis && sudo chmod 0440 /etc/sudoers.d/luis && sudo chown root:root /etc/sudoers.d/luis` (with password — broad grant is gone, but `%sudo` keeps password sudo working) OR drop to console / use `lwadmin-JM831T`. Edge cases noted: `systemctl status <svc> --no-pager` does NOT match the allowlist because sudo treats trailing args as part of the command identity (`status SVC` ≠ `status SVC --no-pager`); in practice the operator either runs without `--no-pager` and `q`s out of the pager, or pipes to `head`. Worth knowing before muscle-memory deploy scripts break. Closes the [HIGH] full passwordless sudo finding. |
| 2026-05-14 | G3 | Shipped. Scope pivoted from "add `deleted_at` to contacts/events/appointments" to "policy doc + guardrail smoke" after the ship-time audit confirmed the CRM-core tier has zero delete code paths (no API endpoint, no service helper, no `session.delete()`, no `DELETE FROM`). Adding nullable columns no code populates would have created false coverage and ongoing read-filter / partial-index burden; the actual MEDIUM risk is undocumented policy that lets a future contributor accidentally hard-delete a Tier-1 financial row or add a CRM delete endpoint whose readers don't filter for it. **New `docs/DATA_RETENTION_AND_DELETE_POLICY.md`** classifies every table into five tiers: Tier 1 — financial soft-delete (invoices, invoice_invitations, quotes, quote_invitations, payments, event_documents — service helpers only, reads filter `deleted_at IS NULL`); Tier 2 — CRM core, append-only (contacts, events, appointments, event_participants, catalog_items, special_orders — no delete path; status fields hide rows); Tier 3 — retention-managed hard-delete (webhook_events purge, attendance geo column scrub from G2); Tier 4 — operational config hard-delete (appointment_availability_rules, appointment_blackouts, staff_shifts, staff_shift_overrides, staff_holidays); Tier 5 — rebuild-children inside parent transactions (invoice/quote line_items, installments, order_discounts, payment_allocations); plus the `appointment_tried_on_items` special case where activity_log is the audit substitute. Each tier has the rule + service helpers + a checklist for adding new tables. **New `tests/test_delete_policy_guardrail_smoke.py`** AST-scans `services/` and `api/routers/` for every `db.delete()` / `session.delete()` / `db_session.delete()` call, resolving each variable back to its ORM model class by walking the enclosing function for the closest prior `var = db.get(Model, ...)` or `var = db.query(Model).first()` binding. Raw `DELETE FROM <table>` is matched via regex on file text. Each call site is checked against an explicit per-file allowlist (`EXPECTED_ORM_DELETES` — 6 sites: StaffShift / StaffShiftOverride in staff_shifts_admin, StaffHoliday in staff_holidays_admin, AppointmentAvailabilityRule / AppointmentBlackout in admin_booking_settings, AppointmentTriedOnItem in sales_tried_on; `EXPECTED_RAW_DELETES` — 8 sites: webhook_events retention, plus the seven invoice/quote/payment rebuild-child DELETEs). Smoke fails on (a) any Tier-1 model appearing as a hard-delete target, (b) any Tier-2 table in raw `DELETE FROM`, (c) any new delete site not yet allowlisted, (d) any allowlisted entry that no longer exists in source (stale-entry detection — prevents the allowlist from silently permitting future re-introduction). Probe-tested at ship time against four scenarios — Tier-1 ORM violation (services/_probe.py with `db.delete(Invoice instance)`) caught; Tier-2 raw violation (`DELETE FROM contacts`) caught; new Tier-4 site not yet allowlisted (StaffShift in a probe file) caught; new unknown raw DELETE caught — all four returned exit 1 with the prescriptive remediation hint pointing at the policy doc. Clean tree returns exit 0 with `6 ORM delete sites, 8 raw DELETE FROM sites scanned` summary. Zero schema changes, zero runtime changes — rollback is `rm docs/DATA_RETENTION_AND_DELETE_POLICY.md tests/test_delete_policy_guardrail_smoke.py`. Closes the [MEDIUM] soft-delete inconsistent audit finding via documentation + enforcement rather than speculative column additions. **Phase G complete (3/3 shipped).** |
| 2026-05-15 | H3 | Shipped. New `docs/CLIENT_DEPLOYMENT_RUNBOOK.md` (8 sections, ~520 lines) turns the H2 inventory into a procedural recipe for provisioning a new same-vertical client. Structure: (0) Decision Gate — same-vertical proceeds, different-vertical STOPS until a workflow-vocabulary generalization slice ships, with five concrete questions (event types other than `quinceanera`? statuses other than the 9 dress-domain values? participant roles other than `quinceanera/dama/chambelan/parent/other`? fit-prep tool vocabulary fit?) that route the operator to STOP if any answer is non-same-vertical; (1) Provisioning Inputs — a fillable form template covering identity (legal name, addresses, contacts, tax), domains (4 surface hosts on one eTLD+1), outbound credentials (SMTP/Twilio/Meta Pixel/Google Ads/Plausible/internal CC), assets (logo, wordmark, hero photos, OG, favicon, widget logo), brand tokens, and infrastructure (VPS provider, backup destination, storage paths, OOB console); (2) Secure Deployment Recipe — concrete shell commands for 10 sub-stages (VPS base hardening with F2/F4/F5/F7 equivalents but explicitly noting F3 stays deferred per parent plan, repo clone, secrets generation for SECRET_KEY/INTEGRATION_TOKEN_KEYS/QUOTE_SIGNATURE_KEY/RESCHEDULE_TOKEN_SECRET/ENRICHMENT_TOKEN_SECRET each unique per deployment, .env construction listing every required and recommended key, Postgres role+DB+migrations with F7 pg_hba lockdown, nginx server blocks + certbot per host with A3 TLS tightening + sysctl hardening, systemd unit with F1 sandbox drop-in, F2 scoped sudo allowlist scaffold, F5 fail2ban jails, F4 file permission + ACL tightening, frontend build, health check), with explicit exit criteria for each stage; (3) Rebrand Steps — DB business_profile UPSERT SQL with every column from the H2 wins list pre-populated, asset replacement bash script preserving filenames so no template paths break, code-edit walkthrough for the H2 hotspots in dependency order (notification_templates.py constants + 20+ string sites with specific line numbers, password_reset.py:71, attendance_pre_close.py:128,132, marketing/index.html + fit-prep.html + styles.css with specific line numbers from H2, portal CSS color tokens, SPA chrome strings across 7 frontend files, booking + fit-prep widget fallback editing), with a verification step that greps for residual `Bella's XV` / Bella's phone / shopbellasxv.com strings and flags missed sites; (4) Security Gate — verification table for all 31 A-G controls (A1 through G3) with a concrete command + expected result for each row, F3 row marked as deferred-accepted; (5) Smoke Gate — go-live smoke subset of 28 backend smokes (union of every auth-adjacent, security-adjacent, and critical-path smoke from the existing suite) invoked serially per project policy + wire-level curl probes through nginx covering bad creds, CSRF middleware, CORS preflight, security headers + 11 browser-flow checks (admin login/logout, sales PIN login/logout, public booking, customer portal, document upload/download, password reset, quote sign + payment record); (6) Cutover and Rollback — pre-cutover pg_dump + .env snapshot + commit-SHA tag, DNS cutover with TTL-aware propagation wait, post-cutover health from at least two geographically separate networks, three rollback procedures (fast = DNS revert, slow = git reset + pg_restore, last-resort = nginx maintenance page); (7) Go/No-Go Signoff — fillable table with 16 rows the operator initials/dates to record the deployment outcome (client name, date, operator, commit SHA, migration count, each gate's ✓/✗, accepted residual risks, sign-off). The runbook closes with the H3 acceptance statement: "A new same-vertical client can be provisioned from the hardened codebase without accidentally shipping a stale or insecure copy." The document is procedural top-to-bottom — every section has shell commands, file paths, or SQL the operator can copy-paste rather than analysis they have to translate. Where a step references a Phase A-G slice's prior change-log entry as source of truth (e.g. the F5 fail2ban filter regex bodies, the F7 pg_hba template), the runbook directs the operator to that entry rather than duplicating the content. Self-contained otherwise. The closure-status banner above the Tracking table was updated to reflect that Phase H is now closed and to name the four residual carries (F3 deferred, lwadmin ticket pending, test-infra eng debt, and the workflow-vocabulary generalization slice that any different-vertical client would need before the runbook applies). Zero code, schema, template, or asset changes. Files touched: `docs/CLIENT_DEPLOYMENT_RUNBOOK.md` (new). Closes the H3 acceptance criterion. **Phase H complete (3/3 shipped). SECURITY_REMEDIATION_PLAN.md is now effectively closed.** |
| 2026-05-15 | H2 | Shipped (audit-only). New `docs/WHITE_LABEL_REBRAND_SURFACE.md` catalogues every place a new-client deployment would need to change. The doc is 7 sections plus a workload-estimate appendix: (1) business identity — business_profile is already broadly parameterized (legal name, address, contacts, tax, terms, reminders, attendance + selfie policy all DB-driven; PDF + portal templates pull from it), with a handful of fallback strings that default to `"Bella's XV"` if the profile is missing (`services/invoice_pdf.py:298`, `services/portal_email.py:90`, `templates/portal/invoice.html:52`); the marketing site, SPA chrome, widget logos, favicons, and brand color tokens are HARDCODE-runbook; (2) domains + hosts — every host-related env knob is wired through `config/settings.py` (`SESSION_COOKIE_DOMAIN`, `CORS_ORIGINS`, `BOOKING_WIDGET_ALLOWED_ORIGINS`, `PUBLIC_SITE_URL`, `WIDGET_PUBLIC_BASE_URL`, `PORTAL_BASE_URL`, `ATTRIBUTION_COOKIE_DOMAIN`), nginx server blocks and certbot certs are HARDCODE-runbook per deployment, plus three hardcoded `shopbellasxv.com` references that survived parameterization (`services/notification_templates.py:505,511` rebooking URL, `frontend/src/pages/BookingWidgetSettings.jsx:49,54,864` embed-code snippet, `frontend/src/pages/SalesStaffSettings.jsx:170` copy, `marketing/fit-prep.html:8,13,14` canonical+OG); (3) outbound channels + secrets — SMTP, Twilio, Meta Pixel, Google Ads, Plausible, internal-notification CC list, per-deployment crypto secrets — all clean PARAM; only `SMTP_FROM_NAME` defaults to `"Bella's XV"` which the runbook flags; (4) customer-facing copy — the heavy hitter, split into 4a-4f: booking widget runtime-overridable via API theme+copy but ships with hardcoded fallbacks (`widgets/bellas-booking-widget.js` lines 790, 837, 1028 + a phone fallback at 762), fit-prep widget is the most-baked-in surface (size chart label, dress style options, back style options, budget tiers all hardcoded — flagged HARDCODE/future-slice because the style/budget vocabularies are quinceañera-specific, not just brand-specific), marketing site is ~15 string sites across `index.html`+`fit-prep.html`+`styles.css` (HARDCODE/runbook), email + SMS templates concentrate the most hardcoded copy of any file — `services/notification_templates.py` ships with `_BOUTIQUE_ADDRESS` + `_BOUTIQUE_PHONE` module constants and 20+ direct `"Bella's XV"` mentions across booking-confirmation, reminder, cancellation, reschedule, internal-notification, enrichment-invitation, and SMS templates, none of which read `business_profile` at render time, plus `services/password_reset.py:71` and `services/attendance_pre_close.py:128,132` carry similar baked-in strings (all HARDCODE/runbook), portal templates are PARAM-clean except for the `"the boutique"` fallback at `templates/portal/invoice.html:52` and the `--primary`+`--text` brand color tokens in `portal/static/portal.css`, PDF templates are fully PARAM via `_resolve_business_header` in `services/invoice_pdf.py:293-331`; (5) workflow vocabulary — the parameterization minefield. Event types (`'quinceanera'` only), event statuses (9 dress-domain values: lead/consulted/sold/on_order/arrived/in_alterations/ready_for_pickup/picked_up/cancelled), participant roles (quinceanera/dama/chambelan/parent/other with a uniqueness constraint forcing exactly one `'quinceanera'` per event), and domain-flavored column names (`quince_theme`, `celebrant_first_name`, `celebrant_last_name`, `boutique_experience_profile_id`, `_CATEGORY_LABELS` with `quince_gown` etc.) are all HARDCODE/future-slice because changing them requires schema CHECK-constraint migrations rippling across service signatures + API contracts + frontend types in lockstep; (6) concrete migration-line-number list for the future-slice cleanup (migration 015 lines 29-31, 32-38, 89-91, 115-117); (7) "what's already cleanly parameterized" — the wins section, recording the business_profile mechanism + PDF pipeline + portal templates + SMTP/Twilio/host config + secrets generation pattern as wins so the H3 runbook can lean on them instead of re-engineering. Workload estimates: ~1 work-day per rebrand for a same-vertical (quinceañera boutique) client, 1-2 weeks of dedicated work plus a future-slice scope for a non-quinceañera client because the workflow vocabulary generalization isn't optional in that case. The doc explicitly closes with what H2 did NOT do (per the audit-only hard rule): no code, schema, template, or asset modified; no parameterization implemented; no new env vars added; no follow-up slices created beyond the HARDCODE/future-slice markers. Investigation used three parallel Explore subagents (workflow language + event statuses, templates + notification copy, widget + marketing + frontend copy) plus direct grep verification of every line-number claim before commit. Files touched: `docs/WHITE_LABEL_REBRAND_SURFACE.md` (new, 224 lines). Closes the H2 acceptance criterion: inventory is complete enough that an operator can read it and predict the rebrand workload for a hypothetical second client; nothing in the codebase was modified. |
| 2026-05-15 | H1 | Shipped (strategy reset). The original Phase H plan committed to a multi-tenant retrofit (tenant_id + RLS) but the existing `WHITE_LABEL_HANDOFF.md` quietly described a fork-and-rebrand model — the two had to stop disagreeing before any code or schema work could start. Decision: per-tenant deployment from one shared hardened codebase wins for the first 1-3 clients. Each new client gets its own VPS (or at minimum an isolated DB), own Postgres database with own role and own pg_hba posture per F7, own `.env` with newly minted secrets (no secret ever copied between deployments — each generates its own `SECRET_KEY`, `INTEGRATION_TOKEN_KEYS`, `QUOTE_SIGNATURE_KEY`, `RESCHEDULE_TOKEN_SECRET`, `ENRICHMENT_TOKEN_SECRET`), own domain set with own certbot certs (`SESSION_COOKIE_DOMAIN` per D3 scoped to the client's apex), own storage paths at mode 750 per F4, own backup destination, own outbound channel config (SMTP / Twilio / Meta Pixel / Google Ads / Plausible — none shared), own monitoring (F5 jails on the client's own access log, C4 audit triggers on the client's own DB). What's NOT per-client: the source code, the test suite, the CI workflow. A security patch lands once and propagates via `git pull` + restart on each deployment. Trade-off explicitly accepted: ops cost scales linearly with client count; this is the right cost to pay until 5+ clients or > 1 work-day-per-patch makes it the wrong cost. Three rejection grounds for the multi-tenant retrofit: (1) data isolation is automatic across process boundaries — no SQL filter can leak across separate DBs, (2) every A-G hardening slice applies cleanly to a fresh deployment with zero new code paths, vs a tenant_id retrofit that would re-validate every slice under cross-tenant query patterns, (3) retrofit work is weeks of touch points across services + RLS + cross-tenant regression coverage whose value only materializes at the third or fourth client. Two artifacts shipped in this commit: (a) `SECURITY_REMEDIATION_PLAN.md` Phase H rewritten — the section header is now "White-label rollout (per-tenant deployment)", the hard-rule callout is replaced by an explicit strategy lock-in pointing at the handoff doc, the H1-H5 slice definitions collapsed to H1-H3 (H1 strategy reset + handoff rewrite shipped here, H2 rebrand surface audit pending, H3 client deployment runbook + go-live gate pending), a new "Future pivot trigger" section names the three conditions under which a multi-tenant retrofit would justify a new major phase (5+ active deployments, > 1 work day to ship a security patch across all of them, or a feature requiring cross-tenant aggregation). The tracking table dropped from 5 H-rows to 3 with H1 marked `shipped 2026-05-15`. The closure-status banner above the table was updated to describe Phase H as a white-label rollout workstream rather than a security gate. (b) `WHITE_LABEL_HANDOFF.md` rewritten in place — the previous version was a single-tenant fork recipe with bash commands; the new version is the strategy doc that names the decision, the rationale, the per-client deployment boundaries enumerated explicitly, what "white-label" means in this model (already-parameterized vs currently-hardcoded buckets, with the explicit guardrail that parameterization work is justified slice-by-slice as real second-client requirements surface, not speculatively), the future-pivot trigger, and pointers to the H2 + H3 artifacts that follow. The bash-command "Fastest Setup Path" section from the prior handoff doc was removed because step-by-step provisioning lives in the H3 runbook by design — keeping it in the handoff doc would let it drift from the authoritative version. Zero code or schema changes; this is a docs-only slice that locks the strategy before H2 begins. Closes the H1 acceptance criterion: SECURITY_REMEDIATION_PLAN.md and WHITE_LABEL_HANDOFF.md describe the same strategy in compatible terms. |
| 2026-05-15 | Parking lot closeout | Closed out the three open parking-lot items as the final sweep before declaring the remediation plan closed. (1) `bellas-white-label-starter.zip` deleted from disk — a 61951916-byte pre-audit snapshot from 2026-05-09 (sha256 `3c0d244de7d97beac64d27e3dd4022941b19227de485242930d67ddc148fbbe7`) that predated every A through G hardening slice; keeping it around would have offered a "convenient" white-label artifact that didn't reflect the secured baseline. Not referenced by any code or script; gitignored via `*.zip`. A fresh starter bundle will be generated after Phase H + white-label prep, derived from the current secured tree. (2) `lwadmin-JM831T` provider account: accepted risk pending Liquid Web confirmation. The account is provider-managed (GECOS `LiquidWeb_Management`), has never logged in interactively per `lastlog`, owns no active processes, and its sudoers drop-in at `/etc/sudoers.d/lwadmin` is maintained by Liquid Web tooling alongside the `lw-auth-verify-ignoreip.local` fail2ban config F5 also preserved. Touching it without provider sign-off could break emergency / support paths. Action item recorded inline: open a Liquid Web ticket to confirm whether the account is still required and what its minimum privileges should be; re-park as "accepted, documented" or schedule a removal slice based on the response. (3) Test-infra import-ordering: explicitly relabeled as deferred engineering debt, NOT security-blocking. Symptom is collection-time only — every smoke passes when invoked directly (`venv/bin/python tests/test_X_smoke.py`), which is the path every Phase A through G slice was actually verified through. Fix paths (autouse `conftest.py` env fixture, or convert smokes to `python -m tests.smoke_X` standalone) are recorded for if/when a cleanup slice is picked up. Added an "Audit closure status" banner above the Tracking table noting that A through E + G are complete, F is 6/7 with F3 deferred, D is complete as of D3, the parking lot is closed-or-accepted, and Phase H is the next workstream not gated by remaining security work. **With this entry, the SECURITY_REMEDIATION_PLAN.md is effectively closed except for F3's accepted/deferred SSH exposure and the future Phase H multi-tenant pivot.** |
| 2026-05-15 | D3 | Shipped. Bearer tokens moved out of `localStorage` into HttpOnly + Secure + SameSite=Lax cookies scoped to `Domain=.shopbellasxv.com`, paired with a readable CSRF nonce cookie that the frontend mirrors into `X-CSRF-Token` on unsafe methods. Per-surface naming so admin and sales contexts stay isolated: admin gets `__Secure-bellas_xv_session` + `__Secure-bellas_xv_csrf`; sales gets `__Secure-bellas_xv_sales_session` + `__Secure-bellas_xv_sales_csrf`. The `__Secure-` prefix is a browser-enforced contract that the cookie MUST be set with `Secure` — a tripwire if anyone ever tries to issue these over plaintext. Backend: new `api/cookies.py` owns the naming + `set_session_cookies` / `clear_session_cookies` helpers, with `SESSION_COOKIE_DOMAIN` env override (defaults to `.shopbellasxv.com`; cleared in smokes so `httpx`'s cookie jar can hand cookies back to `testserver`). `database/auth.py` lost the `OAuth2PasswordBearer` Depends and gained `resolve_request_token(request)` which tries the admin session cookie first, then the sales session cookie, then falls back to the `Authorization` header — disambiguating via the request's `Origin` header when both surface cookies are somehow present (an owner who also stylists). `get_current_user` + `get_current_user_with_scope` now take a `Request` directly and route through the resolver, so every existing dependency-tree consumer (`require_admin_scope`, `require_sales_scope`, `require_any_scope`) inherits the cookie path without touching the call sites. Login routes (`api/routers/auth.py`, `api/routers/sales_auth.py`) take a `Response` parameter and call `set_session_cookies` after minting the JWT; logout routes clear both cookies before returning 204 (alongside the existing D2 `bump_token_version`, which remains the authoritative revocation primitive — the cookie clear is just the browser-visible signal). The legacy `access_token` field is still returned in the JSON body so smokes / curl / any scripts using `Authorization: Bearer` keep working through the transition. New `api/middleware/csrf.py` enforces double-submit on POST/PATCH/PUT/DELETE only when a session cookie is present (header-bearer callers skip CSRF entirely, which is what keeps the smoke suite green without per-test CSRF plumbing). Path exemptions cover the credential-bootstrap endpoints (`/api/auth/login`, `/api/auth/password-reset/`, `/api/sales/auth/pin`), the anonymous booking widget (`/api/booking/`), and the HMAC-signed webhook ingest (`/api/integrations/webhooks/`). Comparison uses `hmac.compare_digest` for constant-time matching; rejection emits a deterministic `detail` (`csrf_token_missing` or `csrf_token_invalid`) so smokes can assert the exact rejection reason. Middleware is registered AFTER `CORSMiddleware` and `SecurityHeadersMiddleware` so it runs FIRST on inbound, but the safe-method check fires before any cookie inspection so CORS preflight (OPTIONS) passes through untouched. Frontend: `frontend/src/services/api.js` adds `withCredentials: true` to the axios instance (otherwise cross-subdomain requests would drop cookies), removes the `Authorization` header request interceptor, and adds a small `readCookie` helper plus a request interceptor that mirrors `__Secure-bellas_xv_csrf` (or its sales twin, picked via `isSalesSubdomain()`) into `X-CSRF-Token` on POST/PUT/PATCH/DELETE. The 401 response interceptor stopped touching `localStorage` (nothing to clear there anymore) and just bounces to `/login`. Stale "Bearer token rides along" comments on the PDF / blob-download helpers were corrected to reflect the cookie-based flow. `frontend/src/contexts/AuthContext.jsx` + `SalesAuthContext.jsx` dropped every `localStorage.getItem` / `setItem` / `removeItem` — the `useEffect` now calls `getMe()` / `salesGetMe()` unconditionally (a 401 means "no live session" and the catch sets user to null), login just holds the returned user object in React state, logout swallows network errors and clears React state regardless. The exports `TOKEN_STORAGE_KEY`, `SALES_TOKEN_STORAGE_KEY`, and `getActiveTokenStorageKey` were deleted; a repo-wide grep confirmed no remaining importers. Smoke: new `tests/test_d3_cookie_auth_smoke.py` covers 13 wire-level assertions against the live FastAPI app via `TestClient(app, base_url="https://testserver")` (https scheme so httpx's cookie jar actually sends back the `Secure`-flagged cookies) — admin login emits both cookies with the right HttpOnly/Secure/SameSite attributes, cookie-only `/auth/me` succeeds with no `Authorization` header, header-bearer `/me` still works on a clean client (legacy path intact), header-bearer POSTs skip CSRF (reach validation instead), cookie POSTs without a CSRF header reject with `csrf_token_missing`, cookie POSTs with a wrong CSRF header reject with `csrf_token_invalid`, the login route is CSRF-exempt even when a stale session cookie is present, cookie POSTs with a matching CSRF header succeed AND the logout response clears both cookies (Max-Age=0), a replayed pre-logout cookie returns 401 (D2's `token_version` bump fired), sales PIN login emits the sales-named pair (and crucially NOT the admin-named pair), cross-surface CSRF rejection works (a sales-cookie POST with an admin-shaped CSRF token rejects), real sales CSRF logout clears the sales pair, and the public booking widget path is genuinely CSRF-exempt (POST reaches the route handler instead of 403'ing). The TestClient + httpx pairing required dropping `SESSION_COOKIE_DOMAIN` to empty in the smoke (httpx's cookie jar enforces Domain matching on send and a `.shopbellasxv.com` cookie won't go back to `testserver`); production keeps the default. 18-smoke regression sweep — D3 + the full auth-adjacent fleet (logout, JWT migration, password reset, password hash, payment refund auth, audit append-only, security headers, business profile, upload validation, confirmation code entropy, clock-in, portal, quote signature HMAC, integration tokens, webhook ingest, booking rate limit, booking token TTL revocation, delete policy guardrail) — all green. Service restart clean; `migrations_applied=65` confirmed live. Wire verification through nginx on `api.shopbellasxv.com`: bad creds → 401 with no `Set-Cookie`, `POST /api/auth/logout` with no cookies → 401 (CSRF skipped, auth fails), `POST /api/auth/logout` with session cookie + no CSRF header → 403 `csrf_token_missing`, `POST /api/auth/login` with stale session cookie + no CSRF header → 422 (login exempt, passes CSRF, hits validation), and the CORS preflight from `https://admin.shopbellasxv.com` requesting `content-type,x-csrf-token` echoes both headers back with `access-control-allow-credentials: true`. nginx config audit confirmed no `proxy_hide_header Set-Cookie` and no `add_header Set-Cookie` rewrites — cookies pass through cleanly. Browser verification (DevTools on `admin.shopbellasxv.com`): post-login Application → Cookies → `api.shopbellasxv.com` shows both cookies with the spec attributes (HttpOnly on session, NOT on CSRF, Secure + SameSite=Lax + Domain=.shopbellasxv.com on both), the embedded JWT decodes to the expected scope/tv claims, Local Storage no longer contains `bellas_xv_token`, the pre-login `/api/auth/me` 401 is expected (the new AuthContext probes the server unconditionally since there's no localStorage gate to short-circuit on). **Acceptance line met:** no bearer token in localStorage, authenticated API calls carry no `Authorization` header, cookies are HttpOnly + Secure, unsafe methods fail without CSRF, logout invalidates replayed cookies. Files touched: `config/settings.py` (+`SESSION_COOKIE_DOMAIN` env knob), `api/cookies.py` (new), `api/middleware/csrf.py` (new), `api/server.py` (+`CSRFMiddleware` registration), `database/auth.py` (resolver + Depends rewire), `api/routers/auth.py` (login/logout cookie set+clear), `api/routers/sales_auth.py` (PIN/logout cookie set+clear), `frontend/src/services/api.js`, `frontend/src/contexts/AuthContext.jsx`, `frontend/src/contexts/SalesAuthContext.jsx`, `tests/test_d3_cookie_auth_smoke.py` (new), plus the rebuilt `frontend/dist` bundle. Closes the [MEDIUM] "Bearer tokens in localStorage" audit finding. **Phase D status: 6/6 shipped (D1, D2, D3, D4, D5, D6).** |
| 2026-05-15 | F3 | Deferred. SSH exposure remains accepted temporarily; fail2ban (sshd jail with 35 historical bans, plus the F5 nginx jails), modern SSH crypto (A4 drop-in), scoped passwordless sudo (F2 allowlist), and the Liquid Web out-of-band console reduce the risk window until a VPN / IP-allowlist architecture is scheduled. Decision sequencing per user: complete D3 (bearer tokens out of localStorage) and any remaining security stragglers first; revisit F3 before the Phase H multi-tenant pivot, or sooner if the sshd fail2ban watchdog shows a material increase in ban volume. Architecture options still on the table: (A) static-IP allowlist via UFW or provider firewall, (B) Tailscale / WireGuard with SSH bound to the VPN interface, (C) hybrid (allowlist + VPN subnet for fallback). No file changes in this entry — pure status flip + rationale capture. |
| 2026-05-14 | E3 | Shipped. New `api/middleware/security_headers.py` registers a `SecurityHeadersMiddleware` on the FastAPI app that emits five baseline headers on every response using `setdefault` semantics. Pre-flight survey of the live hosts (`curl -sI https://admin.` / `sales.` / `shopbellasxv.com`) confirmed nginx already emits four headers — `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options` (`DENY` on admin/sales, `SAMEORIGIN` on the marketing host), `Referrer-Policy: strict-origin-when-cross-origin` — so the middleware uses those exact values as fallbacks and contributes `Permissions-Policy` as the new layer nginx wasn't emitting. The `setdefault` pattern means any upstream-set header value wins (verified by an ad-hoc TestClient probe that sets `X-Frame-Options: SAMEORIGIN` on a route; the middleware leaves it alone). `Permissions-Policy` is scoped to actual frontend feature usage: `camera=(self)` for sales clock-in selfie (`navigator.mediaDevices.getUserMedia`), `geolocation=(self)` for sales clock-in + admin staff-locations, `fullscreen=(self)` for PDF preview, and **every other feature** (accelerometer, ambient-light-sensor, autoplay, battery, bluetooth, display-capture, document-domain, encrypted-media, gyroscope, hid, idle-detection, magnetometer, microphone, midi, payment, picture-in-picture, publickey-credentials-get, screen-wake-lock, serial, sync-xhr, usb, xr-spatial-tracking) denied with empty allowlist `()`. CSP deliberately not set in this slice per the user's spec — Vite-built admin SPA + Google Fonts preconnects + the styled-component inline-style patterns need a real `report-only → enforce` staged rollout that's its own follow-up. Better to ship the four header fallbacks + Permissions-Policy now and not break the SPA than to land a half-tuned CSP that prints console errors at every page load. New `tests/test_security_headers_smoke.py` covers all eight user-spec items: HSTS matches the production nginx value, nosniff present, Referrer-Policy correct, X-Frame-Options DENY, Permissions-Policy includes camera/geolocation `self` grants AND denies microphone/payment/usb, headers fire on multiple status codes (200 + 401), the setdefault semantics let an upstream-set header win, and CSP is asserted absent (so a future accidental add lands in the smoke first). Live probes after restart: loopback `/api/health` shows all 5 headers; `api.shopbellasxv.com/api/health` shows the app's 5 plus nginx's 4 — the four overlap (HSTS / nosniff / X-Frame / Referrer) appear duplicated in the response because nginx's `add_header` is additive rather than idempotent. Values are identical between the two layers so browsers see no behavioral difference; the duplicate is cosmetic. Admin SPA at `admin.shopbellasxv.com` still loads (200, Vite bundle reference intact). 17-smoke regression sweep — all green. Service restart clean. **Phase E complete (3/3 shipped).** Follow-ups noted: CSP (own slice, needs Vite-aware design pass) and SVG XSS on the logo path (from E1's change log). |
