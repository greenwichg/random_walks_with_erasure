"""pagination.py — reusable pagination strategies for catalog queries.

A tiny strategy abstraction so search paginates through an object rather than hardcoded LIMIT/OFFSET
math sprinkled across the store + service. Today only :class:`OffsetPagination` exists; a future
keyset ``CursorPagination`` can implement the same two methods (``apply`` + ``meta``) and slot into
``store.search_feed_articles`` and the search service with **no signature change**.
"""

from __future__ import annotations

import dataclasses


class Pagination:
    """Strategy interface: apply page bounds to a SQL ``select``, and describe the page for the
    response envelope. Subclasses implement both."""

    def apply(self, stmt):
        raise NotImplementedError

    def meta(self, total: int) -> dict:
        raise NotImplementedError


@dataclasses.dataclass(frozen=True)
class OffsetPagination(Pagination):
    """Classic ``limit`` / ``offset`` paging. ``limit <= 0`` means "no limit" (one page of everything);
    page metadata is still reported."""

    limit: int = 60
    offset: int = 0

    @classmethod
    def from_params(cls, limit: "int | None" = 60, offset: "int | None" = 0,
                    *, max_limit: int = 200) -> "OffsetPagination":
        """Build from (possibly untrusted) request params: clamp ``limit`` to ``[0, max_limit]`` and
        ``offset`` to ``>= 0``."""
        lim = 60 if limit is None else int(limit)
        lim = max(0, min(lim, max_limit))
        off = max(0, int(offset or 0))
        return cls(limit=lim, offset=off)

    def apply(self, stmt):
        if self.limit > 0:
            stmt = stmt.limit(self.limit)
        return stmt.offset(self.offset)

    def meta(self, total: int) -> dict:
        """``page`` / ``pageSize`` / ``hasMore`` / ``remainingPages`` for a total row count."""
        if self.limit > 0:
            page = (self.offset // self.limit) + 1
            pages = (total + self.limit - 1) // self.limit if total else 0
            return {
                "page": page,
                "pageSize": self.limit,
                "hasMore": (self.offset + self.limit) < total,
                "remainingPages": max(0, pages - page),
            }
        return {"page": 1, "pageSize": total, "hasMore": False, "remainingPages": 0}
