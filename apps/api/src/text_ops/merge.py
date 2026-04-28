"""Content merging — preserve both perspectives when dedup decides MERGE."""


def merge_content(existing: str, new: str) -> str:
    """Merge new content into existing, preserving both perspectives.

    Simple strategy: append new info that isn't already in existing.
    """
    existing_words = set(existing.lower().split())
    new_sentences = [s.strip() for s in new.split(".") if s.strip()]

    additions = []
    for sentence in new_sentences:
        sentence_words = set(sentence.lower().split())
        # If less than 50% of sentence words are in existing, it's new info
        if sentence_words and len(sentence_words & existing_words) / len(sentence_words) < 0.5:
            additions.append(sentence)

    if not additions:
        return existing  # Nothing new to add

    return existing.rstrip() + "\n" + ". ".join(additions) + "."
