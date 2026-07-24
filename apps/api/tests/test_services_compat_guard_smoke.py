"""Guard: active application source must not use the services/ compatibility
package.

Phase 3 relocated all services into modules/. The flat services/ package that
remains exists SOLELY so immutable historical migrations (which import a service
at upgrade() time) still resolve. Nothing else may import from services.* — new
code and new migrations use modules.* paths. This guard scans active source and
fails if any non-exempt file imports from services.*, so the compatibility
surface can't quietly grow back into a general shim.
"""

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Directories that make up active application source.
SCAN_DIRS = ["modules", "api", "workers", "config", "database"]

# Files allowed to import from services.*:
#   - the compatibility package itself
#   - the two immutable historical migrations that require it
#   - this guard and the compat contract test
ALLOWED = {
    "database/migrations/061_integration_tokens_encrypt.py",
    "database/migrations/062_quote_signature_hmac_and_immutability.py",
}


def _imports_services(py: Path) -> list[int]:
    """Return line numbers where `py` imports from the flat services.* path."""
    hits: list[int] = []
    try:
        tree = ast.parse(py.read_text(), filename=str(py))
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "services" or mod.startswith("services."):
                hits.append(node.lineno)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "services" or a.name.startswith("services."):
                    hits.append(node.lineno)
    return hits


def main() -> None:
    violations: list[str] = []
    for d in SCAN_DIRS:
        root = _REPO_ROOT / d
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            rel = str(py.relative_to(_REPO_ROOT))
            if rel in ALLOWED:
                continue
            for ln in _imports_services(py):
                violations.append(f"{rel}:{ln}")

    if violations:
        raise AssertionError(
            "active application source imports from the services.* compatibility "
            "package (use modules.* instead):\n  " + "\n  ".join(sorted(violations))
        )
    print(f"services_compat_guard smoke ok (scanned {', '.join(SCAN_DIRS)})")


if __name__ == "__main__":
    main()
