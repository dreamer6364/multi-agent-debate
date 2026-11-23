"""Map internal exceptions to friendly, user-facing messages.

Adapters log the real exception (with traceback) and show the user only the
friendly text — no raw SDK internals leak into a channel.
"""
from __future__ import annotations

from .providers import MalformedOutput, ProviderRateLimited, ProviderRefusal


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, ProviderRefusal):
        return "The model declined to analyze that message."
    if isinstance(exc, ProviderRateLimited):
        return "The analysis service is busy or out of quota — please try again in a bit."
    if isinstance(exc, MalformedOutput):
        return "I couldn't read the model's response — please try again."
    return "Something went wrong analyzing that — please try again."
