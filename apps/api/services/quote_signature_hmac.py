"""Permanent compatibility API for historical database migrations."""

from modules.deals.services.quote_signature_hmac import compute_hmac

__all__ = ["compute_hmac"]
