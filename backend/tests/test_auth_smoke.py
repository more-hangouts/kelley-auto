import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://bellas_xv_user:bellas_xv_pass@localhost:5432/bellas_xv",
)
os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from database.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

# Password roundtrip
hashed = hash_password("hunter2")
assert verify_password("hunter2", hashed), "password verify failed"
assert not verify_password("wrong", hashed), "password verify accepted wrong password"


# Token roundtrip — using a duck-typed user object. `role` is required
# now that JWTs carry a `scope` claim derived from the user's role.
class FakeAdminUser:
    id = 1
    token_version = 0
    role = "admin"


class FakeSalesUser:
    id = 2
    token_version = 0
    role = "sales"


admin_token = create_access_token(FakeAdminUser())
admin_claims = decode_access_token(admin_token)
assert admin_claims["sub"] == "1"
assert admin_claims["tv"] == 0
assert admin_claims["scope"] == "admin", admin_claims

sales_token = create_access_token(FakeSalesUser())
sales_claims = decode_access_token(sales_token)
assert sales_claims["sub"] == "2"
assert sales_claims["scope"] == "sales", sales_claims

# Explicit scope override is rejected when invalid.
try:
    create_access_token(FakeAdminUser(), scope="root")
except ValueError:
    pass
else:
    raise AssertionError("create_access_token accepted invalid scope")

print("auth smoke ok")
