"""Choosing Suggestions: plain, deterministic code, never a model (ADR-0006, #18, #46).

The pipeline, stage by stage:
1. **Sources:** the asked-for Products. An unknown or discontinued one is skipped with a warning.
2. **Candidates:** the sources' Bundle partners. Only if too few survive the filters, similar Products
   from embedding similarity (pgvector, Product to Product), at or above the similarity floor.
3. **Filters:** never a discontinued Product, one with no Variant available (untracked counts as
   available), or a source itself. A similar Product must also be outside the category of the source it
   is similar to, so the fallback finds complements (a moisturiser for a cleanser), not substitutes
   (another cleanser). With several sources, each excludes only its own category (owner's decision, #46):
   asked about a moisturiser and a cleanser, the moisturiser may still bring a similar cleanser.
   Then the memory filters (e.g. "already owns it").
4. **Scoring:** Bundles by highest weight, then by how many sources point to them; similar Products by
   similarity. Then the memory boosts (e.g. "matches a stated preference"). Bundles always rank first.
5. **Top N.** Ties break by Product id, so the same input always gives the same output. A Suggestion
   that several sources lead to is credited to the strongest one, the earlier source on a tie.

The memory filters and boosts are empty until #29. With none, which covers every guest, the ranking is
exactly the rules above.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from tillhand.core.constants import SUGGESTION_SIMILARITY_FLOOR, SUGGESTIONS, UCP_VERSION
from tillhand.core.deadlines import StepTimeout
from tillhand.core.errors import business_error, timeout_error
from tillhand.models.db import SimilarProduct, SuggestionSource
from tillhand.models.domain import Product, category_key, category_prefixes
from tillhand.models.ucp import (
    CapabilityEntry,
    ErrorResponse,
    GetSuggestionsArguments,
    Message,
    MessageInfo,
    MessageWarning,
    Suggestion,
    SuggestionKind,
    SuggestionReason,
    SuggestionsResponse,
    UcpResponseMeta,
)
from tillhand.services import catalog_mapper
from tillhand.services.catalog import featured_first


class SuggestionStore(Protocol):
    """The Suggestion queries (`integrations.neon.catalog.NeonCatalogStore`; a fake in tests)."""

    async def suggestion_sources(self, ids: list[str]) -> list[SuggestionSource]: ...

    async def similar_products(
        self, *, sources: dict[str, list[str]], exclude_ids: list[str], floor: float, limit: int
    ) -> list[SimilarProduct]:
        """Each source's `limit` nearest Products at or above `floor`. `sources` maps each source id to
        the category keys its similar Products must stay out of."""
        ...


class SuggestionService(Protocol):
    """`get_suggestions`, the Suggestions Extension's one tool."""

    async def get_suggestions(
        self, arguments: GetSuggestionsArguments
    ) -> SuggestionsResponse | ErrorResponse: ...


@dataclass(frozen=True)
class Candidate:
    product: Product
    kind: SuggestionKind
    source: str
    """The source it is credited to: the strongest pairing, or the nearest source."""
    score: float
    """A Bundle's weight, or a similarity."""
    support: int = 1
    """How many sources point to it (Bundles only)."""
    personalised: tuple[str, ...] = field(default=())


CandidateFilter = Callable[[Candidate], bool]
"""True keeps the candidate."""

Boost = Callable[[Candidate], Candidate]
"""Raises (or lowers) a candidate's score, naming what it used in `personalised`."""


class StoreSuggestionService:
    def __init__(
        self,
        store: SuggestionStore,
        *,
        floor: float = SUGGESTION_SIMILARITY_FLOOR,
        memory_filters: Sequence[CandidateFilter] = (),
        memory_boosts: Sequence[Boost] = (),
    ) -> None:
        self._store = store
        self._floor = floor
        self._memory_filters = memory_filters
        self._memory_boosts = memory_boosts

    async def get_suggestions(
        self, arguments: GetSuggestionsArguments
    ) -> SuggestionsResponse | ErrorResponse:
        request = arguments.catalog
        if request.product_ids is None:
            return business_error(
                code="not_supported",
                content="Suggestions for a Cart (cart_id) are not supported yet. Pass product_ids instead.",
                severity="recoverable",
                path="$.catalog.cart_id",
            )
        ids = list(dict.fromkeys(request.product_ids))
        limit = request.limit
        try:
            found = {s.id: s for s in await self._store.suggestion_sources(ids)}
            messages = _skipped(request.product_ids, found)
            sources = [found[id] for id in ids if id in found and found[id].status == "active"]
            source_ids = [s.id for s in sources]
            chosen = self._rank(self._filter(_bundle_candidates(sources), source_ids))[:limit]
            if len(chosen) < limit and sources:
                chosen += await self._similar(sources, chosen, limit - len(chosen))
        except StepTimeout as exc:
            return timeout_error(exc.step)
        if not chosen:
            messages.append(
                MessageInfo(code="no_suggestions", content="Nothing qualifies to suggest for these Products.")
            )
        return SuggestionsResponse(
            ucp=UcpResponseMeta(
                version=UCP_VERSION,
                capabilities={SUGGESTIONS.name: [CapabilityEntry(version=SUGGESTIONS.version)]},
            ),
            suggestions=[_suggestion(c) for c in chosen],
            messages=messages or None,
        )

    async def _similar(
        self, sources: list[SuggestionSource], chosen: list[Candidate], gap: int
    ) -> list[Candidate]:
        categories = {s.id: sorted({category_key(c) for c in s.categories}) for s in sources}
        source_ids = list(categories)
        taken = {c.product.id for c in chosen}
        rows = await self._store.similar_products(
            sources=categories,
            exclude_ids=sorted({*source_ids, *taken}),
            floor=self._floor,
            # Each source's nearest `gap + len(chosen)`, so the filters can drop some and still fill the gap.
            limit=gap + len(chosen),
        )
        # Filter each (source, Product) pair before merging: a Product in one source's category may still
        # complement another source, and is then credited to that one.
        pairs = [Candidate(r.product, "similar", r.source_id, r.similarity) for r in rows]
        kept = [
            c
            for c in self._filter(pairs, source_ids, categories=categories)
            if c.product.id not in taken and c.score >= self._floor
        ]
        return self._rank(_strongest_per_product(kept, source_ids))[:gap]

    def _filter(
        self,
        candidates: list[Candidate],
        source_ids: list[str],
        *,
        categories: dict[str, list[str]] | None = None,
    ) -> list[Candidate]:
        """`categories`: for similar candidates, each source's category keys, which its pairs stay out of."""

        def outside_its_sources_category(c: Candidate) -> bool:
            own = (categories or {}).get(c.source, [])
            return not set(own) & set(category_prefixes(c.product.categories))

        def keep(c: Candidate) -> bool:
            product = c.product
            return (
                not product.discontinued
                and any(product.is_available(v) for v in product.variants)
                and product.id not in source_ids
                and outside_its_sources_category(c)
                and all(memory(c) for memory in self._memory_filters)
            )

        return [c for c in candidates if keep(c)]

    def _rank(self, candidates: list[Candidate]) -> list[Candidate]:
        boosted = list(candidates)
        for boost in self._memory_boosts:
            boosted = [boost(c) for c in boosted]
        return sorted(boosted, key=lambda c: (-c.score, -c.support, c.product.id))


def _skipped(requested: list[str], found: dict[str, SuggestionSource]) -> list[Message]:
    messages: list[Message] = []
    for index, id in enumerate(requested):
        path = f"$.catalog.product_ids[{index}]"
        if id not in found:
            messages.append(MessageWarning(code="not_found", content=f"No Product {id}; skipped.", path=path))
        elif found[id].status != "active":
            messages.append(
                MessageWarning(
                    code="discontinued", content=f"Product {id} is discontinued; skipped.", path=path
                )
            )
    return messages


def _bundle_candidates(sources: list[SuggestionSource]) -> list[Candidate]:
    """One candidate per partner: its strongest pairing's weight and source, and how many sources name it."""
    best: dict[str, Candidate] = {}
    for source in sources:  # in input order, so an equal weight stays credited to the earlier source
        for partner in source.bundles:
            id = partner.product.id
            current = best.get(id)
            if current is None:
                best[id] = Candidate(partner.product, "bundle", source.id, partner.weight)
            elif partner.weight > current.score:
                best[id] = replace(
                    current, source=source.id, score=partner.weight, support=current.support + 1
                )
            else:
                best[id] = replace(current, support=current.support + 1)
    return list(best.values())


def _strongest_per_product(candidates: list[Candidate], source_ids: list[str]) -> list[Candidate]:
    """One candidate per Product: its highest similarity to any source, credited to that source."""
    order = {id: i for i, id in enumerate(source_ids)}
    best: dict[str, Candidate] = {}
    for c in sorted(candidates, key=lambda c: (-c.score, order.get(c.source, len(order)))):
        best.setdefault(c.product.id, c)
    return list(best.values())


def _suggestion(candidate: Candidate) -> Suggestion:
    product = candidate.product
    shown = catalog_mapper.search_product(product, featured_first(product, product.variants))
    return Suggestion(
        **dict(shown),
        reason=SuggestionReason.model_validate(
            {"kind": candidate.kind, "from": candidate.source, "personalised": list(candidate.personalised)}
        ),
    )
