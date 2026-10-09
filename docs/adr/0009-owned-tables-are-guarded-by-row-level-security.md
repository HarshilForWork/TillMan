# Owned tables are guarded by Postgres Row-Level Security

Per-Customer and per-Platform isolation already has two layers (#11, #42):
- every data function for owned rows requires an `Owner`, so pyright fails without one;
- the isolation fixture proves a stranger gets the same answer as for an id that doesn't exist.

Both live in our code. A query that forgets its owner filter, and gets past both, would still leak. The owner chose to add a third layer in the database itself (#45): **Row-Level Security (RLS)**, where Postgres filters rows by a policy whatever the query says.

## Decisions

- **Two roles.** `tillhand_admin` owns the tables and is used only by migrations, seeding and the admin scripts (`ADMIN_DATABASE_URL`). `tillhand_app` owns nothing and runs the server (`DATABASE_URL`). Every owned table has `ENABLE` and `FORCE ROW LEVEL SECURITY`, so ownership alone never bypasses a policy.
- **The owner is set per transaction,** because a session setting doesn't survive Neon's pooler. One helper in `integrations/neon/` opens a transaction for an `Owner`, or for `System(reason)`, and first runs `set_config('app.owner_platform', …, true)` and `set_config('app.owner_customer', …, true)`, or `app.system`. Nobody hand-writes `set_config`.
- **A system path for actors that serve no Customer:** the Razorpay webhook receiver and the reconcile script. They set `app.system`, which policies accept. Their code paths are named and few, so they're easy to grep.
- **Child tables check their parent** (`EXISTS` on the parent's primary key), rather than copying owner columns. Today that's `carts`, `cart_lines` and `idempotency_keys`.
- **Every future owned table ships its policy in the migration that creates it,** and a test fails if any table with owner columns lacks a policy. Admin and lookup tables (`customers`, `merchant_api_keys`, `platforms`) carry no RLS. The app role gets only the privileges it needs on them.

## Considered Options

- **Decline, relying on layers 1 and 2:** recommended at first, overruled by the owner in favour of database-enforced isolation.
- **Running webhooks as the admin role:** rejected, because the busiest write path would bypass RLS entirely.
- **Owner columns copied onto every child table:** rejected, because the data is duplicated and can drift.

## Consequences

- **One extra round trip per owned transaction** (the `set_config` call): a few milliseconds when the server sits next to Neon.
- **Two connection strings** to configure per deployment, which fits the Terraform plan.
- **The payment tables** (`checkouts`, `payment_intents`, `holds`, `orders`, `order_events`) get their policies when #51–#53 create them. The webhook path uses `System("razorpay_webhook")`.
