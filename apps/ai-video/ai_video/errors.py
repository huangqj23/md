"""Errors shared by the provider adapters.

Providers report moderation refusals in different shapes (HTTP 400 at submit time, a failed task
later, numeric codes, free-text messages), so refusals are recognised by keyword. A refusal is
tracked separately from other failures because the refusal rate is one of the bench results.
"""
import re

_REJECT_PATTERNS = re.compile(
    r"sensitive|\brisk|polic(y|ies)|moderat|deepfake|real[ _-]?(person|human)|\bfaces?\b|portrait"
    r"|敏感|违规|审核|人脸|肖像|\b1026\b|\b1301\b",
    re.IGNORECASE,
)


class ProviderError(Exception):
    """A request failed; the message is shown in the report."""


class Rejected(ProviderError):
    """Content moderation refused the input or the output."""


def classify(code, message) -> type[ProviderError]:
    text = f"{code or ''} {message or ''}"
    return Rejected if _REJECT_PATTERNS.search(text) else ProviderError
