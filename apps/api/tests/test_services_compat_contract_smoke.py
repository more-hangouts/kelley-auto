"""Contract for the permanent migration-compatibility package (services/).

Phase 3 moved services into modules/. Two immutable historical migrations still
import from the flat services.* path at upgrade() time, so services/ re-exports
exactly those symbols. This test proves the compatibility imports resolve to the
CURRENT implementations, so a fresh migration replay works.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def main() -> None:
    # The two symbols historical migrations 061 / 062 import.
    from services.integration_tokens import encrypt as compat_encrypt
    from services.quote_signature_hmac import compute_hmac as compat_hmac

    # They must be the same objects as the current module-path implementations.
    from modules.core.services.integration_tokens import encrypt as real_encrypt
    from modules.deals.services.quote_signature_hmac import (
        compute_hmac as real_hmac,
    )

    assert compat_encrypt is real_encrypt, (
        "services.integration_tokens.encrypt is not the current implementation"
    )
    assert compat_hmac is real_hmac, (
        "services.quote_signature_hmac.compute_hmac is not the current implementation"
    )

    # The compat package must expose ONLY these symbols (no wildcard drift).
    import services.integration_tokens as it
    import services.quote_signature_hmac as qh

    assert it.__all__ == ["encrypt"], it.__all__
    assert qh.__all__ == ["compute_hmac"], qh.__all__

    print("services_compat_contract smoke ok")


if __name__ == "__main__":
    main()
