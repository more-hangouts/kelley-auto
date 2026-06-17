"""Script-style smokes for the Bellas XV backend.

Each ``test_*_smoke.py`` runs as a standalone script:

    venv/bin/python tests/test_invoices_smoke.py

The smokes share the dev/prod database and seed their own rows, then
clean up on exit. They are NOT pytest-friendly (helpers are named
``check_*`` to keep pytest from collecting them).

Known footgun — do not run smokes in parallel against the same DB.
Several smokes mutate the singleton ``numbering_state`` row to allocate
the next invoice / quote / payment number; the rollover scenario in
``test_invoices_smoke`` even resets the year counter explicitly.
Concurrent runs against the shared row produce non-deterministic
sequence numbers and can collide with each other's expectations.
Run them serially:

    for t in tests/test_*_smoke.py; do venv/bin/python "$t" || break; done

If parallel execution becomes a real time-sink, the fix is either a
file lock around numbering allocation or a per-run schema, not a
test-by-test patch.
"""
