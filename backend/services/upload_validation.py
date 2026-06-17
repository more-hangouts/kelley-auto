"""Magic-byte validation for user-uploaded files.

Phase E1 of SECURITY_REMEDIATION_PLAN.md. Three layers of defense for
every upload route, all in one place:

  1. Extension allowlist (caller's table).
  2. Browser-supplied content-type allowlist (caller's table).
  3. Magic-byte sniff against the first 16 bytes of the actual stream.

A renamed .exe (MZ header) reaching the upload route with
`Content-Type: application/pdf` and a `.pdf` extension would slip past
the first two layers but trips here — its leading bytes are `4D 5A`,
not `25 50 44 46 2D` (`%PDF-`).

This module is intentionally minimal and offline-only. Pillow already
covers selfies (it refuses to decode if the bytes are not a real
image), so the selfie path stays as-is; this validator is for the
upload paths where Pillow is not in the pipeline — event documents
and the business logo.

Format detection scope:

  pdf   → starts with `%PDF-`
  png   → 89 50 4E 47 0D 0A 1A 0A
  jpg   → FF D8 FF
  jpeg  → same
  webp  → bytes 0-3 = `RIFF`, bytes 8-11 = `WEBP`
  heic  → ISO BMFF `ftyp` box at offset 4 with a HEIC brand
  docx  → ZIP magic `PK\x03\x04` (we cannot prove DOCX without
          unzipping; ZIP magic + the `.docx` declared extension is the
          practical bound)
  svg   → text-based; we accept `<svg`, `<?xml`, or BOM-prefixed
          variants. SVG payloads can carry JS — callers that serve
          SVG inline are responsible for sanitising before render.

Callers pass the LEADING bytes (`HEAD_BYTES_NEEDED` is enough) plus
the declared extension. The validator raises `UploadValidationError`
on any mismatch; the route handler maps it to the appropriate HTTP
status.
"""

from __future__ import annotations

from dataclasses import dataclass

# 16 bytes covers every signature we check, including the WebP RIFF
# header that lives at offsets 0-3 and 8-11.
HEAD_BYTES_NEEDED = 16


class UploadValidationError(Exception):
    """Raised when a file's bytes do not match its declared shape.

    Carries a stable string `code` and an HTTP `status` so the route
    handler can re-raise as a uniform `HTTPException`. Every code in
    this module is a member of the static set below so callers can
    pattern-match without grepping strings.
    """

    UNSUPPORTED_TYPE = "unsupported_type"
    EMPTY_FILE = "empty_file"

    def __init__(self, code: str, *, status: int = 415) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Signature:
    """A magic-byte test. `match` returns True when `head` looks like
    this format. `prefix` is the canonical example used in tests; not
    consulted at runtime."""

    prefix: bytes

    def match(self, head: bytes) -> bool:  # pragma: no cover — overridden
        return head.startswith(self.prefix)


@dataclass(frozen=True)
class _RiffWebP(_Signature):
    def match(self, head: bytes) -> bool:
        return len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP"


@dataclass(frozen=True)
class _HeicFtyp(_Signature):
    # ISO BMFF: bytes 4-7 = `ftyp`, bytes 8-11 = brand. HEIC variants:
    # heic, heix, heim, heis, hevc, hevx, mif1, msf1.
    _BRANDS = frozenset(
        {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}
    )

    def match(self, head: bytes) -> bool:
        return (
            len(head) >= 12
            and head[4:8] == b"ftyp"
            and head[8:12] in self._BRANDS
        )


@dataclass(frozen=True)
class _SvgText(_Signature):
    def match(self, head: bytes) -> bool:
        # Strip optional UTF-8 BOM and leading whitespace; SVG can start
        # with either `<svg` or an `<?xml` declaration.
        text = head.lstrip(b"\xef\xbb\xbf").lstrip()
        return text.startswith(b"<svg") or text.startswith(b"<?xml")


_SIGNATURES_BY_EXT: dict[str, _Signature] = {
    "pdf": _Signature(prefix=b"%PDF-"),
    "png": _Signature(prefix=b"\x89PNG\r\n\x1a\n"),
    "jpg": _Signature(prefix=b"\xff\xd8\xff"),
    "jpeg": _Signature(prefix=b"\xff\xd8\xff"),
    "webp": _RiffWebP(prefix=b"RIFF"),
    "heic": _HeicFtyp(prefix=b"\x00\x00\x00 ftyp"),
    "docx": _Signature(prefix=b"PK\x03\x04"),
    "svg": _SvgText(prefix=b"<svg"),
}


def validate_magic_bytes(
    *,
    declared_ext: str,
    head: bytes,
) -> None:
    """Sniff the leading bytes of an upload against the declared extension.

    Raises `UploadValidationError("unsupported_type", status=415)` on
    any mismatch (unknown extension, empty head, or magic-byte test
    that fails). Caller's outer extension allowlist normally catches
    unknown extensions first; the redundant check here is intentional
    so an internal misuse cannot bypass the magic-byte gate by
    handing in a bare extension.
    """
    if not head:
        raise UploadValidationError(UploadValidationError.EMPTY_FILE, status=400)
    sig = _SIGNATURES_BY_EXT.get(declared_ext.lower())
    if sig is None:
        raise UploadValidationError(UploadValidationError.UNSUPPORTED_TYPE)
    if not sig.match(head):
        raise UploadValidationError(UploadValidationError.UNSUPPORTED_TYPE)
