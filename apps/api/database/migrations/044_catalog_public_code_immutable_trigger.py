from sqlalchemy import text


def upgrade(connection) -> None:
    # Catalog SKU obfuscation Phase 7: public_code is customer-facing
    # audit identity. Service code already refuses to patch it; this
    # trigger protects against raw SQL and future migration scripts.
    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION prevent_catalog_public_code_update()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.public_code IS DISTINCT FROM OLD.public_code THEN
                    RAISE EXCEPTION 'catalog_items.public_code is immutable'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'catalog_public_code_immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    connection.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_catalog_public_code_immutable
            ON catalog_items
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TRIGGER trg_catalog_public_code_immutable
            BEFORE UPDATE OF public_code ON catalog_items
            FOR EACH ROW
            EXECUTE FUNCTION prevent_catalog_public_code_update()
            """
        )
    )
