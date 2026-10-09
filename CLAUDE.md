# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TillHand

A product that gives D2C Merchants their own agent-ready store. **Each Merchant gets its own separate deployment**; one deployment never serves two Merchants, and multi-tenant stays a non-goal. A deployment contains a UCP-conformant MCP server over that Merchant's catalog in Neon, a UPI/Razorpay payment handler, and a server-side authorization layer. There's also a thin agent client. Built for the Razorpay Buildathon (Track 01).

**Two websites, never to be confused:**

- **The TillHand site** is our own product site, where a Merchant comes to get its MCP server. It's `tillhand.vercel.app`, which also hosts TillHand's Extension schemas, so its reversed domain is our namespace: `app.vercel.tillhand.*` (see "Conform to UCP" below).
- **The demo Storefront** is a test website for a synthetic example Merchant, the skincare brand, deployed on Vercel. It's one Merchant's own site: it serves that Merchant's `/.well-known/ucp`, pointing at that Merchant's MCP deployment.

**The skincare brand is only an example.** Nothing in the MCP server, the schema or the tools may assume skincare. Everything is generic across Merchants; see the catalog domain-model decision.

## Repository state

**Early code.** A uv project with a `src/` layout (Python 3.11). It holds the UCP wire models (UCP `2026-08-25`), the project constants and Extensions, the error builders, and the **catalog data layer** (#12): the catalog domain model, the Neon schema (Alembic), the SQL, Pinecone embeddings, the `CatalogService` implementation with its UCP mapper, and two seed catalogs. It also holds the **MCP server** (#34): the three UCP catalog tools plus `get_suggestions` (#46, the Suggestions Extension) over stateless streamable HTTP at `/mcp`, inside a FastAPI app (`main.py`) with a `/healthz`. Every tool call first resolves the Platform's profile (#37): pre-approved in the `platforms` table (our Harness), otherwise fetched with SSRF guards (`integrations/profile_fetch.py`); a bad one is JSON-RPC `-32001`. It holds the **Owner pattern** (#42): every tool is labelled `public` or `owner_scoped` in `api/mcp/access.py`, owner-scoped tools receive the caller's `Owner`, and `tests/support/isolation.py` proves strangers learn nothing. It holds the **cart tools** (#50): `create_cart`, `get_cart`, `update_cart` and `cancel_cart` over `carts`, `cart_lines` and `idempotency_keys` (migration `0002`), re-priced live on every read. It holds the **Merchant door** (#41): `/merchant/mcp`, guarded by Merchant API keys (`merchant_api_keys`, hashes only) with `TillHand-Customer` resolving to a `customers` row; Carts there belong to the key's profile plus the Customer. The pre-approved Platforms now live in Neon's `platforms` table, seeded from `data/platforms.json` and loaded at startup; a Merchant assistant's profile is key-bound and refused on `/mcp`. There's no checkout or payments code yet.

```bash
uv sync                                         # install from uv.lock
uv run pytest -q                                # full suite (offline; the live Neon tests skip)
uv run pytest tests/models/ucp/test_models.py   # a single file
uv run pyright                                  # type check (standard mode)
uv run ruff check . && uv run ruff format --check .

uv run alembic upgrade head                                      # migrate the database in DATABASE_URL
uv run python scripts/seed_catalog.py data/seeds/skincare.json  # load a catalog file, embed what changed
uv run python scripts/seed_platforms.py                         # load data/platforms.json into `platforms`
uv run python scripts/issue_key.py --profile <url> --label <who>  # a Merchant API key, printed once
uv run python scripts/revoke_key.py <key id>                     # immediate; `issue_key.py --list` shows ids
uv run python scripts/search_smoke.py "serum for oily skin"     # real searches, by hand
uv run python scripts/suggestions_smoke.py [floor]              # every Product's Suggestions and similarities
TILLHAND_NEON_TESTS=1 uv run pytest -m neon                     # the SQL and the production app, live

uv run uvicorn tillhand.main:production_app --factory --port 8000  # serve /mcp over Neon + Pinecone
```

- **Migrations are Alembic with hand-written SQL** (`op.execute`) in `migrations/versions/`. There are no SQLAlchemy models and no autogenerate: SQLAlchemy is only Alembic's plumbing, and the app uses asyncpg directly. Migrations run on the same pooled `DATABASE_URL` as the app.
- **asyncpg behind Neon's pooler works as-is,** statement cache included (probed with 120 concurrent tasks, #12), and takes Neon's `channel_binding` parameter unchanged. A session-level `SET` doesn't survive the pooler, so per-database settings go in a migration (`alter database ... set`).
- **State lives in Neon as plain relational columns,** never JSON blobs or repo files (owner's rule, 5 Oct 2026: deployments must be reproducible with Terraform on any cloud). An Owner is two columns; Cart lines are rows. The one exception is a stored idempotency response, kept verbatim so a retry replays it byte for byte.
- **A catalog file is the `Catalog` model** (`models/domain/catalog.py`), one per Merchant. The two synthetic ones are in `data/seeds/`. Re-seeding is idempotent, and it marks Products missing from the file as discontinued rather than deleting them.

### Code layout (layer-based; agreed 24 Sep 2026)

```text
src/tillhand/
  main.py          app factory: FastAPI + the MCP server; one DB pool and one httpx2 client at startup
  core/            app-wide plumbing, no business logic: constants.py, errors.py, config, deadlines
  api/             ALL endpoints, nothing else: mcp/ (tools under UCP names, both doors), routes/ (FastAPI)
  models/          ALL Pydantic classes: ucp/ (wire), domain/ (our Product/Order/...), db/ (rows)
  services/        business logic, the functions defined once and wrapped by every door (ADR-0001)
  integrations/    the ONLY code that leaves the process: neon/ (pool + all SQL), razorpay/, pinecone.py,
                   prompt_guard.py
  utils/           small helpers that know nothing about the business
src/tillhand_agent/  the Harness, a separate package that reaches the server only over HTTP
tests/             mirrors src/ (tests/models/ucp/, tests/core/, ...); shared fixtures in tests/support/
migrations/ (Alembic)  evals/  data/seeds/  scripts/  web/site/ (TillHand site)  web/storefront/ (Demo Merchant)  vendor/
```

Folders are created when their first code lands, not before. The rules that make the layout work:

- **Dependencies point one way:** `api → services → integrations`. Everything may import `models`, `core` and `utils`, and **nothing imports `api`**. `tillhand_agent` never imports `tillhand`.
- **`integrations/` is the only door out of the process,** whether Neon, Razorpay, Pinecone or model inference. It's the seam where evals swap in fakes (Razorpay mocked, recorded embedding vectors), and the one place to check the risky rules: no DB connection held across slow work, a timeout on every call, retries only when a call is safe to repeat, and **never Razorpay's refund API**.
- **`utils/` stays harmless.** If a helper mentions a Merchant, an Order or any business rule, it belongs in `services/`.
- **Import models from their subpackage:** `from tillhand.models.ucp import Product`, never from the module file inside it.
- **The UCP spec is vendored** at `vendor/ucp/v2026-08-25/`: official schemas, catalog and overview docs, and scaffolds. Tests validate our models against it (`tests/support/ucp_spec.py`). **Never edit it**; to move to a new UCP version, vendor that version alongside and change `UCP_VERSION` in `core/constants.py`.
- **Extension names** live only in `core/constants.py`, derived from the TillHand site URL. They are `app.vercel.tillhand.{service}.{capability}`, and their schemas must be served from `https://tillhand.vercel.app`, or Platforms silently drop them. `tests/core/test_constants.py` enforces this with the spec's own check.

Intended stack, from the plan and `.env.example`: Python (async throughout), the `mcp` SDK over streamable HTTP, FastAPI for non-MCP endpoints, Pydantic v2 at every boundary, Neon Postgres with pgvector, the Razorpay REST API (test mode), Prompt Guard 2 on CPU via `torch`, DeepEval for evals, Docker on Railway.

## Where the intent lives

Read these before proposing anything; they disagree with each other on purpose and the later one wins.

- `Idea.md` — the build plan: scope, the two claims, the eleven components, build order, non-goals, known constraints.
- `updates.md` — an adversarial verification pass over `Idea.md` (21 Sep 2026). Every load-bearing claim is marked VERIFIED / PARTIALLY VERIFIED / UNVERIFIABLE / CONTRADICTED. **UNVERIFIABLE means "not confirmed," not "false."** Section C ranks the corrections by severity.
- `CONTEXT.md` — the glossary. Use its terms verbatim in issue titles, test names, identifiers and prose; it lists the synonyms to avoid.
- `docs/adr/` — decisions already made. If your work contradicts one, say so explicitly rather than quietly overriding it.
- `docs/architecture/payments.md` — **the payments design**: the Cart, Checkout, PaymentIntent, Hold and Order lifecycles, with state and sequence diagrams, every decision with its rejected alternatives, the failure scenarios, and interview Q&A. **Read it before touching cart, checkout, order, webhook or Razorpay code.** It's a living document: any payment decision (#16, #17, …) extends it in the same pass as its ADR.
- `.env.example` — every secret the project needs, annotated with the constraint that bites for each one. It is the fastest map of the external dependencies.

Ecosystem facts here are dated and move monthly (UCP releases, handler census, NPCI/Razorpay pilot scope). Re-verify version-dependent claims against primary sources rather than trusting the file's timestamp.

## Architecture invariants

These are the decisions everything else hangs off. Breaking one silently invalidates the project's claims.

- **Authority is server-side, never in the agent.** The agent — ours or a third party's — only ever *proposes*. Every guardrail, scope check and policy decision is deterministic code in the server, because an agent we don't control never runs our client code. **No LLM anywhere in the request path.**
- **Refunds are requests, never executions.** The server records and evaluates a refund request and returns a refusal or an approval requirement; a human executes it in the Razorpay dashboard. We never call Razorpay's refund API. This deliberately keeps us outside the payment aggregator's obligations.
- **Conform to UCP `2026-08-25`, don't invent tool contracts.** Catalog/cart/checkout/order tool names are the standard's. Extensions go under `app.vercel.tillhand.shopping.*` (`suggestions`, `refund_request`). So does our payment handler, since authority binding covers handlers too. `2026-08-25` made namespace authority binding a **MUST**: a capability whose schema host doesn't match its namespace is **silently rejected**, with no error. Business "no" outcomes (out of stock, not found, timeouts) are normal results with `ucp.status: "error"` and `messages[]`, never `isError` or an exception.
- **Our `Checkout` and `Order` (in Neon) and the `PaymentIntent` (one Razorpay order per attempt) are different objects with different lifecycles.** One Checkout accumulates several PaymentIntents across retries, and an Order is created only when one is captured. Collapsing them produces an ambiguous audit trail. See ADR-0007 and `docs/architecture/payments.md`.
- **The Harness talks to our own MCP server as a real client over HTTP**, not by importing the tool functions — that is what keeps the third-party-agent claim honest. Tools are defined once as plain Python functions; the server and any direct caller wrap that single definition. See `docs/adr/0001`.
- **Payment completion is asynchronous.** UPI has no agent-suppliable token: the handler returns a payment intent (link/QR), the human approves in their bank app, and the order completes on webhook. Webhooks are at-least-once and can arrive out of order, so **state moves forward only**, and that's enforced in the database, not just in code. `captured` beats every other state, because UPI can authorise late. A missed webhook is healed by reconcile-on-read and a reconcile script, through the same transition code.
- **Vectors live in Neon via pgvector**, not a separate vector store. See `docs/adr/0002`.

## Payments: rules any payment code must keep

The full design and its reasons are in `docs/architecture/payments.md` and ADR-0007 (#30). The invariants, short form:

- **Forward only, enforced in the database.** Use a conditional `UPDATE … WHERE status IN (<allowed from>)`, never "set the state from the payload". A trigger rejects backward moves. Every Razorpay webhook is inserted into an append-only `webhook_events` table (PK `x-razorpay-event-id`) **before** it's applied, in the same transaction, so a replay is a no-op.
- **`captured` beats everything.** A `failed`, `cancelled` or `expired` PaymentIntent may still become `captured` (UPI late authorisation, up to 3 days), and nothing leaves `captured`. Use automatic capture.
- **Capture always creates an Order,** even for an expired or cancelled Checkout, flagged `late_payment` / `oversold`. We never auto-refund, and we never call the refund API.
- **Never two payable links.** A retry cancels the previous attempt's link before creating a new one. A refused cancel means that link was paid.
- **One PaymentIntent per attempt,** with `receipt = {checkout}-{attempt}`, which is Razorpay's idempotency key.
- **Holds:** `complete_checkout` locks Variant rows `FOR UPDATE` **in id order** (to avoid deadlocks) for milliseconds, checks stock minus active Holds, and inserts a Hold that expires with the link. Expiry is lazy, with no clean-up job. The capture transaction (event insert + transitions + Hold → stock decrement + Order) is the **only** place stock goes down, and stock never goes below zero.
- **One transition function** serves webhooks, reconcile-on-read (`get_checkout` asks Razorpay after 30 s of staleness) and the reconcile script. Never write a second path.
- **`complete_checkout` shape:** short DB transaction → commit → Razorpay call (no connection held) → short DB transaction.
- **Webhook receiver:** HMAC over the raw body with the *webhook* secret, then one DB-only transaction, then 200 within 5 s. Nothing slow inside.
- **Idempotency keys** (`complete_checkout`, `cancel_checkout`, `cancel_cart`) live in Neon for 48 h, keyed `(key, caller, operation)`, and are written in the same transaction as the effect. A store failure fails closed (503).

## Coding rules

Standing rules from the project owner. They apply to every line of code, including prototypes and eval harness code.

- **Async throughout.** Every entry point (MCP tools, HTTP endpoints, webhook handlers, the Harness loop) and every function that touches I/O (Neon, Razorpay, HTTP, files) is `async def`. **Never block the event loop:** sync-only libraries (the Razorpay SDK) and CPU-bound work (Prompt Guard inference) run through `asyncio.to_thread` or an executor. Use async clients only — an async Postgres driver, `httpx2.AsyncClient`, never `requests`. It's `httpx2`, not `httpx`: `mcp` 2.x depends on `httpx2` (Pydantic's successor to `httpx`), and its client transport takes an `httpx2.AsyncClient`. One HTTP library means one pooled client per process. The only sync code allowed is what must be sync (Pydantic validators) or pure computation with no I/O.
- **Pydantic validates every boundary, in and out.** Pydantic v2 models for every tool input and output, every endpoint request and response, every webhook payload, and every response from an external API, parsed at the boundary before anything uses it. No raw `dict`s crossing a function boundary that touches the outside world. The UCP mapper produces Pydantic models, not dicts.
- **FastAPI for HTTP endpoints** that aren't MCP protocol traffic: the Razorpay webhook receiver, and the catalog API the Vercel Storefront reads. The Storefront never keeps its own copy of the catalog, so the site and the agent can't disagree on price or stock. Check how the `mcp` 2.x streamable-HTTP app mounts alongside FastAPI against the installed SDK, not from memory.
- **Design for scale by default:**
  - **Stateless processes.** No per-Customer or per-Session state held in memory across requests. It lives in Neon, so any instance can serve any request.
  - **Shared resources are pooled.** One DB pool and one HTTP client per process, created at startup and reused, never opened per request.
  - **Bounded queries.** Every list or search is paginated and has a limit. No N+1 queries. Every filter in a hot path has an index.
  - **Idempotent writes.** Every write triggered from outside (webhooks, retried tool calls) is safe to replay, because webhooks arrive at least once.
  - **Timeouts on every external call**, with retries only where the call is idempotent.
  - **Never hold a DB connection across slow work.** Acquire a connection, run the queries, release it, *then* await anything slow (Razorpay, Prompt Guard, embedding, any HTTP call), then re-acquire to write the result. A connection or an open transaction is never held across an `await` on anything that isn't the database. The only exception is when atomicity genuinely requires it, e.g. a stock decrement or a state transition. Even then the transaction holds only DB statements and stays as short as possible, and the code says why.
  - **Every endpoint and tool has a deadline, and fails with a reason.** When a request runs out of time, it returns a structured, Pydantic-modelled timeout error naming the step that overran (e.g. `razorpay.create_order`), instead of hanging or dropping the connection. A slow dependency produces a clear, explained failure, not a stuck worker.

## Constraints that will waste your time if you forget them

- **Razorpay test mode: 30 payment links per business, ever.** UPI payment links are unavailable in test mode, and links are browser-only (no headless completion). Mock the Razorpay boundary in the eval suite; spend real links only on hand-run demos.
- **ngrok is blacklisted by Razorpay webhooks**; `zrok` is the recommended tunnel. Local dev cannot otherwise receive webhooks.
- **`mcp` 2.x is a breaking rewrite.** Pin the SDK line explicitly. MCP code recalled from memory or older tutorials is likely wrong. **Set `stateless_http=True`.** Verified on `mcp` 2.2.0 with two replicas behind a round-robin proxy:
  - A client speaking the `2026-07-28` protocol is stateless no matter how the flag is set. Our own 2.x `Client` negotiates that version by default (`mode="auto"`).
  - A client using the legacy handshake (`2024-11-05` … `2025-11-25`) gets a server-side session unless the flag is `True`. Without sticky sessions, which Railway doesn't have, its second request fails with `Session not found`. Third-party agents may still speak the legacy protocol.
- Without `TransportSecuritySettings(allowed_hosts=[...])` every request returns **HTTP 421** (localhost-only DNS-rebinding protection is on by default). Ours comes from the `ALLOWED_HOSTS` setting (a JSON list). A request carrying any browser `Origin` gets **403**, deliberately: Platforms call from servers.
- **UCP tools use the low-level `Server`, not `MCPServer`'s `@tool`** (`api/mcp/server.py`). The decorator turns bad arguments into `isError` results and wraps a union return in `{"result": ...}`; both break UCP's MCP binding. Tools publish no `outputSchema`, because a client would check an `ErrorResponse` against it.
- **On the legacy handshake path the SDK sends an unhandled exception's own text to the client** as JSON-RPC error code 0 (the modern path sanitizes it). `call_tool` therefore catches everything that isn't an `MCPError`, logs it and answers `-32603`.
- `torch` must be installed from the CPU index, or the image pulls the CUDA stack for a GPU that will never exist.
- Prompt Guard 2 is **gated** (request access from Meta on HF, manual approval) under the Llama 4 Community License, with a **512-token context** — chunk long catalog copy, and expect false positives on benign product text.
- DeepEval: `ToolCorrectnessMetric` is only deterministic if `available_tools` is **not** passed. Guardrail scenarios use `ToolPermissionMetric` (threshold 1.0, no model). Groq has no native DeepEval support — try the LiteLLM route before subclassing `DeepEvalBaseLLM`.
- Razorpay's Python SDK is sync-only — wrap calls in `asyncio.to_thread` — and collapses HTTP errors to a bare string.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`HarshilForWork/TillMan`), managed via the `gh` CLI. **Pass `--repo HarshilForWork/TillMan` on every invocation** — the `origin` remote uses an SSH alias host that `gh` cannot auto-detect — and make sure `gh` is authenticated as `HarshilForWork`, or writes fail with a misleading 404. See `docs/agents/issue-tracker.md`.

Work is tracked as a wayfinder map (issue #1) with child tickets labelled `wayfinder:research` / `prototype` / `grilling` / `task`.

### Triage labels

The five canonical triage roles, using their default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
