"""
Shared utility functions.
"""

import hashlib


def compute_canonical_hash(title: str, url: str) -> str:
    """
    Compute canonical hash for deduplication.

    Normalizes title and URL to catch duplicates across surfaces.
    Same content may appear on multiple platforms with slight variations.

    Args:
        title: Original title
        url: Content URL

    Returns:
        SHA256 hash (64 character hex string)

    Example:
        >>> compute_canonical_hash("Breaking News!", "https://example.com/news")
        'a1b2c3d4...'
    """
    # Normalize: lowercase + strip whitespace
    normalized_title = title.lower().strip()
    normalized_url = url.lower().strip()

    # Combine for hashing
    combined = f"{normalized_title}|{normalized_url}"

    # Return SHA256 hash
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()
