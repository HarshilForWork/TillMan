"""Neon Postgres: the pool and all SQL.

**Rules for owned data** (Carts now; Checkouts, Orders and refund requests later), from #11 and #42:
- **Owned ids are random UUIDs, never sequential.** The database makes them (`gen_random_uuid()`), so an
  id can't be guessed from another one. Until signatures (#22), "this Platform created it" rests on a
  profile URL the caller merely claims, so an unguessable id is half of the protection.
- **Every function that touches owned data takes an `Owner`** as a required argument, and its SQL
  matches both owner columns (`owner_platform`, and `owner_customer` with `is not distinct from`, so a
  guest's null matches only a guest). Pyright rejects a call without one.
- **Someone else's row and a missing row look the same.** A query for another owner's id returns
  nothing, exactly as for an id that never existed, so callers can't tell the two apart.
- **The owner is plain columns,** never an encoded string, like every other piece of state here.
"""
