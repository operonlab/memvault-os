"""Word-level Jaccard similarity — unified from memvault + docvault implementations."""


def jaccard_word_overlap(a: str, b: str) -> float:
    """Compute Jaccard similarity of word sets.

    Used for content dedup, contradiction detection, and topic overlap.
    """
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)
