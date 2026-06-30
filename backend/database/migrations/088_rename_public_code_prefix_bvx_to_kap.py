"""Rename the catalog public-code prefix BVX -> KAP (Bella's XV -> Kelley Autoplex).

`public_code` is the customer-facing identifier minted for every catalog row
(vehicles included) by ``services/catalog_service._assign_catalog_public_code``.
It shipped as ``BVX-NNNNN`` — inherited Bella's XV brand residue. Kelley Autoplex
is a different business in a different vertical, and this is pre-launch: only
seed rows and CRM-imported inventory exist and no code has been handed to a
customer yet, so we renumber the prefix to ``KAP-NNNNN`` now while it is cheap.

The 5-digit sequence is preserved 1:1 (``BVX-00042`` -> ``KAP-00042``); only the
3-letter prefix changes, so UNIQUE-ness and the ``numbering_state`` counter are
untouched and minting continues from the same sequence. The format CHECK
constraint (`chk_catalog_items_public_code_format`, from migration 041) is
swapped to enforce the KAP shape.

Idempotent: once every row is KAP and the constraint already enforces the KAP
shape, the UPDATE matches nothing and the constraint swap is a no-op.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # 1. Drop the old BVX-format CHECK first — a BVX->KAP value would violate
    #    the `^BVX` constraint mid-flight, so the rename UPDATE needs it gone.
    connection.execute(
        text(
            "ALTER TABLE catalog_items "
            "DROP CONSTRAINT IF EXISTS chk_catalog_items_public_code_format"
        )
    )

    # 2. public_code is immutable by design — migration 044 installs a
    #    BEFORE UPDATE trigger that RAISEs on any public_code change. Lift it
    #    for this single controlled rename, then restore the exact same guard
    #    in step 5 so the protection survives the migration.
    connection.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_catalog_public_code_immutable "
            "ON catalog_items"
        )
    )

    # 3. Rename every existing code in place: BVX-NNNNN -> KAP-NNNNN.
    #    Blanket UPDATE covers the seed vehicles AND all CRM-imported inventory.
    connection.execute(
        text(
            "UPDATE catalog_items "
            "SET public_code = 'KAP-' || substring(public_code from 5) "
            "WHERE public_code LIKE 'BVX-%'"
        )
    )

    # 4. Re-add the format CHECK, now enforcing the KAP shape.
    connection.execute(
        text(
            "ALTER TABLE catalog_items "
            "ADD CONSTRAINT chk_catalog_items_public_code_format "
            "CHECK (public_code ~ '^KAP-[0-9]{5}$')"
        )
    )

    # 5. Restore the immutability trigger (identical to migration 044). The
    #    function itself was never dropped; only the trigger was lifted.
    connection.execute(
        text(
            "CREATE TRIGGER trg_catalog_public_code_immutable "
            "BEFORE UPDATE OF public_code ON catalog_items "
            "FOR EACH ROW "
            "EXECUTE FUNCTION prevent_catalog_public_code_update()"
        )
    )

    # Guard: no BVX-prefixed codes survive the rename.
    remaining = connection.execute(
        text("SELECT COUNT(*) FROM catalog_items WHERE public_code LIKE 'BVX-%'")
    ).scalar()
    assert remaining == 0, f"{remaining} BVX-prefixed public_code rows remain"
