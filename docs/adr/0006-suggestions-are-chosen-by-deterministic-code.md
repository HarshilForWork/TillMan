# Suggestions are chosen by deterministic code, not a model

TillHand's Suggestions (`get_suggestions`, the `app.vercel.tillhand.shopping.suggestions` Extension) are the growth half of Claim A. A reader might expect an "AI upsell" to be picked by a model. It isn't. The server chooses Suggestions with plain code:
- **Candidates:** Bundle partners first, ranked by weight and then by how many Cart items point to them. Gaps are filled from embedding similarity over pgvector, restricted to **other categories** than the source and above a similarity floor.
- **Filters:** discontinued Products, Products with no Variant in stock, anything already in the Cart, and the source itself.
- **Output:** the top N (default 3, at most 10), each with a structured `reason`: `bundle` or `similar`, the Product it came from, and any personalisation applied.

The AI sits around this, not inside it. The embedding model decides what's "similar", and the agent's model (ours or a Platform's) decides when to offer a Suggestion and how to phrase it (#18).

## Why

- **A Platform's agent already has a model.** What it lacks is the Merchant's knowledge: which Products go together, and what's in stock. The server supplies facts, and the agent supplies conversation.
- **There's no LLM in the request path** (CLAUDE.md). The same input always gives the same Suggestions: fast, free and testable.
- **The growth claim needs it.** Lift is measured by running the same simulated Sessions with and without the tool. A model choosing Suggestions would add noise to the very number being measured.
- **Honest reasons need it.** Code that chose a Suggestion knows why, so the agent can say "the Merchant pairs this with your cleanser" instead of inventing "dermatologists recommend".

## Considered Options

- **Raw similarity as the fallback:** rejected. Similarity finds substitutes (a second serum for someone buying a serum). Excluding the source's category keeps the fallback a cross-sell.
- **Parsing sizes like "30ml" from option strings for "better value" upsells:** rejected, because it assumes skincare-like units. Merchant-stated Variant quantities are a follow-up.

## Consequences

- **Memory plugs in later without a rewrite.** The pipeline has empty slots for filters (already owns it) and boosts (matches a stated preference), to be filled by #29. Personalised results go only to an identified Customer (#11), and the reason says what was personalised.
- **Where a model genuinely helps is offline, on the Merchant's side:** drafting Bundles from the catalog for the Merchant to approve. It's never in the request path.

## Refinements while building (#46, 5 Oct 2026)

- **Each source excludes only its own category** (owner's decision). Asked about a moisturiser and a micellar water together, the moisturiser may still bring a similar cleanser; only a cleanser similar to the micellar water is excluded. With one Product, the rule is unchanged.
- **The similarity floor is 0.48**, tuned on the skincare seed with `llama-text-embed-v2` (`scripts/suggestions_smoke.py`): every cross-category pair at or above it agrees on skin type or purpose, and the first mismatches sit at 0.477 and 0.472. It depends on the catalog and the embedding model, so each deployment may override it (`SUGGESTION_SIMILARITY_FLOOR`).
- **The Extension extends both `dev.ucp.shopping.catalog.search` and `dev.ucp.shopping.catalog.lookup`.** UCP defines no `dev.ucp.shopping.catalog` capability, and negotiation prunes an Extension none of whose parents survive.
