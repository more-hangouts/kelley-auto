import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_

from database.auth import hash_password
from database.connection import SessionLocal
from database.models import User


def _prompt(label: str) -> str:
    value = input(f"{label}: ").strip()
    if not value:
        print(f"ERROR: {label} cannot be empty", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> int:
    print("Create the first Bellas XV admin user.")
    username = _prompt("username")
    email = _prompt("email")
    full_name = _prompt("full name")

    if "@" not in email:
        print("ERROR: email must contain '@'", file=sys.stderr)
        return 1

    password = getpass.getpass("password: ")
    if len(password) < 8:
        print("ERROR: password must be at least 8 characters", file=sys.stderr)
        return 1
    confirm = getpass.getpass("confirm password: ")
    if password != confirm:
        print("ERROR: passwords do not match", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        existing = (
            db.query(User)
            .filter(
                or_(
                    func.lower(User.email) == email.lower(),
                    User.username == username,
                )
            )
            .first()
        )
        if existing is not None:
            field = "email" if existing.email.lower() == email.lower() else "username"
            print(f"ERROR: a user with that {field} already exists", file=sys.stderr)
            return 1

        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Created admin user: {user.username} ({user.email}) — id {user.id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
