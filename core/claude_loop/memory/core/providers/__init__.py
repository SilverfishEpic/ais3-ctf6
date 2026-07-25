"""Summarizer backends.

Only ``ais3`` ships today.  Providers are the *only* place a vendor API appears; the
domain model never sees a provider-specific object.
"""

from .ais3 import AIS3Summarizer

__all__ = ["AIS3Summarizer"]
