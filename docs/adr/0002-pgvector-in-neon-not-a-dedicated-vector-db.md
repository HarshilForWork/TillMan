# Catalog vectors live in Neon via pgvector, not a dedicated vector database

The initial plan named Qdrant for catalog search. The catalog is 20–50 SKUs, and Neon is Postgres, which supports pgvector natively — so embeddings can sit in the same database and the same transaction as the catalog rows they describe. Running Qdrant alongside Neon would mean a second stateful service to deploy, seed, and keep in sync, buying scale characteristics we are nowhere near needing.

## Considered Options

- **Qdrant**: rejected. Real benefits, but they start paying off orders of magnitude above our catalog size, and the catalog/vector sync problem is work we would be taking on today for a benefit we would not see.
- **Postgres full-text search, no vectors at all**: viable at this size and worth remembering as the fallback if pgvector proves fiddly. Rejected only because semantic search is closer to how an agent phrases a query.
