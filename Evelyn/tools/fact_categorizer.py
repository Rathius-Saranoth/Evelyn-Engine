# fact_categorizer.py
# date created: 2026-09-14
# date modified: 2026-10-17 12:44:40
# tags: #facts, #categories, #taxonomy, #recategorization, #hysteresis

"""
fact_categorizer.py — Deprecated Category Governance & Taxonomy Compatibility Shim.

Maintained exclusively for backward compatibility with historical migrations and tests.
The Cat## fast memory category taxonomy was retired in 000.008.045 in favor of controlled
vocabulary faceted tags and direct entity attribution.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import evelyn_config as cfg

logger = logging.getLogger("evelyn.fact_categorizer")

_CATEGORY_PATTERN = re.compile(
    r"^Cat(\d{2})-([A-Z])$", re.IGNORECASE
)


def validate_and_normalize_category(
    cat_str: str | None, subject: str | None = None
) -> str | None:
    """Validate and normalize a category string to canonical Cat##-[UA] format."""
    user_code = getattr(cfg, "SUBJECT_CODE_USER", "U")
    assistant_code = getattr(cfg, "SUBJECT_CODE_ASSISTANT", "A")

    if not cat_str or not isinstance(cat_str, str) or not cat_str.strip():
        return None

    cat_str = cat_str.strip()
    if cat_str.endswith(".md"):
        return None

    num_match = re.search(r"(?i)(?:cat|kat|cad|kad|ca|ka|c|k)\s*(\d{1,2})", cat_str)
    if not num_match:
        return None

    num = int(num_match.group(1))
    if not (1 <= num <= 16):
        return None

    cat_base = f"Cat{num:02d}"

    # Determine suffix: map legacy 'R' -> 'U' and 'E' -> 'A'
    suffix = None
    suffix_match = re.search(r"(?i)(?:-|/|\s)?([erua])$", cat_str)
    if suffix_match:
        raw_code = suffix_match.group(1).upper()
        if raw_code in ("R", "U"):
            suffix = user_code
        elif raw_code in ("E", "A"):
            suffix = assistant_code
    else:
        after_num = cat_str[num_match.end():].upper()
        has_u = "U" in after_num or "R" in after_num
        has_a = "A" in after_num or "E" in after_num
        if has_u and not has_a:
            suffix = user_code
        elif has_a and not has_u:
            suffix = assistant_code

    if not suffix and subject:
        subj_lower = subject.lower()
        if getattr(cfg, "USER_NAME", "Alex").lower() in subj_lower:
            suffix = user_code
        elif getattr(cfg, "ASSISTANT_NAME", "Evelyn").lower() in subj_lower:
            suffix = assistant_code

    if not suffix:
        suffix = user_code

    return f"{cat_base}-{suffix}"


def remediate_database_categories() -> int:
    """Remediate malformed or legacy category codes (deprecated, no-op after category retirement)."""
    return 0


def is_recategorization_suppressed(*_args: Any, **_kwargs: Any) -> bool:
    """Check whether a recategorization should be suppressed (deprecated, returns True)."""
    return True


async def audit_category_classifications(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    """Audit category placement (deprecated, returns empty list after category retirement)."""
    return []
