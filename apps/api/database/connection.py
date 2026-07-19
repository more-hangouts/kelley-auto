import os

from sqlalchemy import create_engine, event, text as _sql_text
from sqlalchemy.orm import declarative_base, sessionmaker

from config.settings import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


# Phase C4: the audit-tables append-only trigger blocks UPDATE/DELETE
# unless the session has `audit_tables.allow_mutation = on`. Test
# cleanup paths legitimately need to DELETE rows after seeding their
# fixtures; production code must NOT bypass. So the bypass is gated on
# the `ALLOW_AUDIT_MUTATION=1` environment variable, which the smokes
# set in their preamble and the systemd unit never sets.
#
# Fired on `checkout` rather than `connect`: the connection pool's
# `reset_on_return = ResetStyle.reset_rollback` wipes session-scoped
# GUCs between borrows, so a one-time `connect` listener only worked
# for the FIRST session that touched each pooled connection. The
# `checkout` event fires every time a connection is borrowed from
# the pool, which is exactly the granularity we need.
if os.getenv("ALLOW_AUDIT_MUTATION") == "1":
    @event.listens_for(engine, "checkout")
    def _allow_audit_mutation(dbapi_connection, _connection_record, _connection_proxy):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("SET audit_tables.allow_mutation = 'on'")
        finally:
            cur.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
