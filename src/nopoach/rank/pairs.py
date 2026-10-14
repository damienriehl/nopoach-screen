"""Canonical identifiers shared by employer-pair signals and ranking."""


def pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)
