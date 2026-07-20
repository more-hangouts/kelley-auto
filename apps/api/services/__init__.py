"""Permanent compatibility surface for immutable historical migrations.

Phase 3 moved every application service into ``modules/<domain>/services/``.
The database migrations are immutable historical artifacts and must stay
byte-for-byte identical, so the two that import a service function at
``upgrade()`` time (061, 062) still reference the flat ``services.*`` path.

This package re-exports ONLY those specific symbols so a fresh migration replay
resolves. It is not a general shim: new application code and new migrations must
use the current ``modules.*`` paths (or migration-local helpers). Do not add
further re-exports here casually — the delete-policy-style guard in
``tests/test_services_compat_guard_smoke.py`` asserts active application source
never imports from ``services.*``.
"""
