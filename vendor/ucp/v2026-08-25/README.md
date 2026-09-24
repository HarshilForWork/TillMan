# Vendored: Universal Commerce Protocol, `v2026-08-25`

Copied verbatim from https://github.com/Universal-Commerce-Protocol/ucp at tag
`v2026-08-25` (commit `cd78fb3`). Apache License 2.0; see `LICENSE`. Do not edit.

- `source/schemas/`: the official JSON Schemas. TillHand's UCP models are tested against them.
- `docs/specification/shopping/catalog/`: the catalog spec pages, whose annotated examples
  (`<!-- ucp:example ... -->`) are used as test fixtures.
- `docs/specification/overview/index.md`: the overview, the source of the namespace authority
  check and its examples table.
- `scripts/scaffolds/`: the spec's own known-valid minimal payloads.

To move to a new UCP version, vendor it alongside this one and update `UCP_VERSION`.
