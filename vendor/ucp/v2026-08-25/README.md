# Vendored: Universal Commerce Protocol, `v2026-08-25`

Copied verbatim from https://github.com/Universal-Commerce-Protocol/ucp at tag
`v2026-08-25` (commit `cd78fb3`). Apache License 2.0; see `LICENSE`. Do not edit.

- `source/schemas/`: the official JSON Schemas. TillHand's UCP models are tested against them.
- `docs/specification/shopping/catalog/` and `docs/specification/shopping/cart/` (`index.md`,
  `mcp.md`): the catalog and cart spec pages, whose annotated examples (`<!-- ucp:example ... -->`)
  are used as test fixtures.
- `source/services/shopping/mcp.openrpc.json`: the MCP binding's tool signatures (e.g. `cancel_cart`
  requires `meta["idempotency-key"]`).
- `docs/specification/overview/index.md`: the overview, the source of the namespace authority
  check and its examples table.
- `scripts/scaffolds/`: the spec's own known-valid minimal payloads (catalog and cart).

The cart pages, `mcp.openrpc.json` and the cart scaffolds were added on 5 Oct 2026 (#50), from the
same tag and commit.

To move to a new UCP version, vendor it alongside this one and update `UCP_VERSION`.
