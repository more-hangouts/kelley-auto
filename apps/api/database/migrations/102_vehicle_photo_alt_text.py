"""Per-photo alt text for vehicle listings.

Vehicle photos live in ``catalog_items.image_urls`` (an ordered JSONB array
of strings, index 0 = cover). Nothing described them: the storefront
gallery rendered ``alt={title}`` on the hero and the literal string
``"View 2"``, ``"View 3"``… on the thumbnails. For a screen-reader user
that is a listing whose fourteen photos are indistinguishable, and for
search engines it is fourteen images carrying no signal.

**Shape: an object keyed by URL, not a parallel array.**

The obvious design — ``image_alts`` as an array aligned index-for-index
with ``image_urls`` — is quietly wrong here, because the photo order is
*editable*. The admin photo manager is a drag-to-reorder grid, and it
saves by PATCHing the whole ``image_urls`` array; "make cover" is the
same operation. Any positional store desynchronises the first time a rep
drags a photo, and the failure is silent and invisible to the person who
caused it: the alt text stays syntactically valid and starts describing
the wrong picture, which is worse than no alt text at all.

Keying by URL makes reordering a no-op for alt text by construction, and
each photo's description follows the photo. Deleting a photo leaves an
unreferenced key, which the service prunes in ``update_catalog_item`` at
the same point it collects removed media blobs.

Readers that want positional data are served by the API, not the column:
``public_vehicle_dto`` walks ``image_urls`` and emits ``photoAlts``
aligned to ``photos``, so the storefront never sees the map.

NOT NULL DEFAULT '{}' with a jsonb_typeof CHECK mirrors how ``image_urls``
was declared in 041 — no NULL/absent third state for readers to handle.
Existing rows get ``{}``: no backfill, alt text is authored, not derived.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE catalog_items
              ADD COLUMN IF NOT EXISTS image_alts JSONB NOT NULL
                DEFAULT '{}'::jsonb
            """
        )
    )

    # Same guard style as chk_catalog_items_image_urls_array (migration
    # 041): keep the container type honest at the DB level so a bad write
    # can't turn the map into an array or a scalar.
    exists = connection.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'chk_catalog_items_image_alts_object'"
        )
    ).first()
    if not exists:
        connection.execute(
            text(
                """
                ALTER TABLE catalog_items
                  ADD CONSTRAINT chk_catalog_items_image_alts_object
                  CHECK (jsonb_typeof(image_alts) = 'object')
                """
            )
        )
