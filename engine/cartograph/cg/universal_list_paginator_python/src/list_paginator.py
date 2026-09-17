from typing import Any


def paginate(items: list[Any], page: int = 1, size: int = 20, max_size: int = 500) -> dict:
    """Return a single page of items plus metadata.

    Pages are 1-indexed. Out-of-range pages return an empty slice with valid
    metadata (so callers never have to branch on "did my page exist"). Size
    is clamped to [1, max_size]. max_size is the caller's safety cap on a
    single page; raise it for power-user surfaces, lower it for constrained
    ones.
    """
    if not isinstance(items, list):
        raise TypeError("items must be a list")
    if not isinstance(page, int) or not isinstance(size, int):
        raise TypeError("page and size must be int")
    if not isinstance(max_size, int) or max_size < 1:
        raise ValueError("max_size must be a positive int")

    size = max(1, min(size, max_size))
    total = len(items)
    total_pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, total_pages))

    start = (page - 1) * size
    end = start + size
    slice_ = items[start:end] if total else []

    return {
        "items": slice_,
        "page": page,
        "size": size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }
