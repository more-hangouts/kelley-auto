"""OCR a photo of a VIN plate/sticker into candidate VINs.

Tesseract reads the glyphs; the VIN **check digit** is what makes an OCR
read trustworthy — a misread ``8``→``B`` or ``5``→``S`` almost always
breaks the position-9 checksum, so checksum-valid candidates rank first
and the rest are returned flagged "verify". Staff always confirm before a
scanned VIN is saved; this only removes the typing.

Reads from: driver door-jamb stickers, dashboard VIN plates, or a photo
of the title/auction sheet. It does NOT read the Code-39 barcode most
door stickers carry (that needs zbar); OCR of the printed characters is
the v1 path, with the checksum as the safety net.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO

import pytesseract
from PIL import Image, ImageOps

from services import vin as vin_util

log = logging.getLogger(__name__)

# The VIN alphabet, handed to Tesseract as a character whitelist so it
# never emits I/O/Q (or punctuation) that a VIN can't contain anyway.
_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
_WHITELIST = f"-c tessedit_char_whitelist={_VIN_CHARS}"

# Multiple page-segmentation modes, unioned: 11 = sparse text (a busy
# sticker with scattered lines), 6 = uniform block, 7 = single text line
# (a cropped VIN reads near-perfectly here). More modes = more chances a
# checksum-valid read appears; the checksum then filters the rest.
_CONFIGS = (
    f"--psm 11 {_WHITELIST}",
    f"--psm 6 {_WHITELIST}",
    f"--psm 7 {_WHITELIST}",
)

# A run of >=17 VIN-alphabet characters; we then slide a 17-wide window.
_RUN_RE = re.compile(rf"[{_VIN_CHARS}]{{17,}}")

_UPSCALE_TARGET = 1600  # upscale small phone crops so glyphs are legible
_MAX_PIXELS = 40_000_000  # reject absurd inputs before Tesseract chews on them


def _preprocess(body: bytes) -> Image.Image:
    """Grayscale, orient, upscale-if-small, autocontrast — the cheap
    transforms that most improve Tesseract on phone photos."""
    img = Image.open(BytesIO(body))
    img = ImageOps.exif_transpose(img)
    if (img.width * img.height) > _MAX_PIXELS:
        raise ValueError("image too large to OCR")
    img = img.convert("L")
    longest = max(img.size)
    if longest < _UPSCALE_TARGET:
        scale = _UPSCALE_TARGET / longest
        img = img.resize(
            (round(img.width * scale), round(img.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return ImageOps.autocontrast(img)


def _candidates_from_text(text: str, out: dict[str, bool]) -> None:
    """Extract VIN candidates from one OCR pass, merging into `out`
    (vin -> check_digit_ok). A checksum-valid read never gets downgraded
    by a later invalid one for the same string."""
    cleaned = text.upper().replace(" ", "")
    for run in _RUN_RE.findall(cleaned):
        for i in range(len(run) - 16):
            window = run[i : i + 17]
            try:
                norm = vin_util.normalize_vin(window)
            except vin_util.VinError:
                continue
            if norm is None:
                continue
            ok = vin_util.vin_check_digit_ok(norm)
            if norm not in out or (ok and not out[norm]):
                out[norm] = ok


def scan(body: bytes) -> tuple[list[dict], str]:
    """Return ``(candidates, ocr_text)``.

    ``candidates`` is a list of ``{"vin", "check_digit_ok"}`` ranked with
    checksum-valid VINs first. Empty when nothing VIN-shaped was read.
    Never raises for bad input — returns ``([], "")`` so the endpoint can
    say "no VIN found, try again".
    """
    try:
        img = _preprocess(body)
    except (Image.DecompressionBombError, OSError, ValueError) as exc:
        log.warning("VIN OCR preprocess failed: %s", exc)
        return [], ""

    found: dict[str, bool] = {}
    texts: list[str] = []
    for config in _CONFIGS:
        try:
            text = pytesseract.image_to_string(img, config=config)
        except (pytesseract.TesseractError, OSError) as exc:
            log.warning("tesseract pass failed (%s): %s", config, exc)
            continue
        texts.append(text)
        _candidates_from_text(text, found)

    ranked = sorted(found.items(), key=lambda kv: (not kv[1], kv[0]))
    candidates = [{"vin": v, "check_digit_ok": ok} for v, ok in ranked]
    return candidates, "\n".join(texts)
