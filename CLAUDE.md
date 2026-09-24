# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TillHand

A product that gives D2C Merchants their own agent-ready store. **Each Merchant gets its own separate deployment**; one deployment never serves two Merchants, and multi-tenant stays a non-goal. A deployment contains a UCP-conformant MCP server over that Merchant's catalog in Neon, a UPI/Razorpay payment handler, and a server-side authorization layer. There's also a thin agent client. Built for the Razorpay Buildathon (Track 01).

**Two websites, never to be confused:**

- **The TillHand site** is our own product site, where a Merchant comes to get its MCP server. It's `tillhand.vercel.app`, which also hosts TillHand's Extension schemas, so its reversed domain is our namespace: `app.vercel.tillhand.*` (see "Conform to UCP" below).
- **The demo Storefront** is a test website for a synthetic example Merchant, the skincare brand, deployed on Vercel. It's one Merchant's own site: it serves that Merchant's `/.well-known/ucp`, pointing at that Merchant's MCP deployment.

**The skincare brand is only an example.** Nothing in the MCP server, the schema or the tools may assume skincare. Everything is generic across Merchants; see the catalog domain-model decision.

## Repository state

**Early code.** A uv project with a `src/tillhand/` layout (Python 3.11). So far it contains only the UCP contract layer, `tillhand.ucp`: the wire types for UCP `2026-08-25`, our Extensions, and the error builders. There's no server, database or payments code yet.

```bash
uv sync                                  # install from uv.lock
uv run pytest -q                         # full suite
uv run pytest tests/ucp/test_models.py   # a single file
uv run pyright                           # type check (standard mode)
uv run ruff check . && uv run ruff format --check .
```

- **Pydantic classes live in a `models/` package inside the layer they belong to,** e.g. `tillhand/ucp/models/` for UCP wire models. Each layer's shapes stay separate (wire shapes apart from database rows), and non-model code (interfaces like `ucp/service.py`, builders, checks) sits beside `models/`, not in it. Callers import from the layer's package (`from tillhand.ucp import Product`), never from `models` directly.
- **The UCP spec is vendored** at `vendor/ucp/v2026-08-25/`: official schemas, catalog docs and scaffolds. Tests validate our models against it (`tests/ucp/spec.py`). **Never edit it**; to move to a new UCP version, vendor that version alongside and change `UCP_VERSION`.
- **Extension names** live only in `src/tillhand/ucp/extensions.py`, derived from the TillHand site URL. They are `app.vercel.tillhand.{service}.{capability}`, and their schemas must be served from `https://tillhand.vercel.app`, or Platforms silently drop them. `tests/ucp/test_namespace.py` enforces this with the spec's own check.

Intended stack, from the plan and `.env.example`: Python (async throughout), the `mcp` SDK over streamable HTTP, FastAPI for non-MCP endpoints, Pydantic v2 at every boundary, Neon Postgres with pgvector, the Razorpay REST API (test mode), Prompt Guard 2 on CPU via `torch`, DeepEval for evals, Docker on Railway.

## Where the intent lives

Read these before proposing anything; they disagree with each other on purpose and the later one wins.

- `Idea.md` — the build plan: scope, the two claims, the eleven components, build order, non-goals, known constraints.
- `updates.md` — an adversarial verification pass over `Idea.md` (21 Sep 2026). Every load-bearing claim is marked VERIFIED / PARTIALLY VERIFIED / UNVERIFIABLE / CONTRADICTED. **UNVERIFIABLE means "not confirmed," not "false."** Section C ranks the corrections by severity.
- `CONTEXT.md` — the glossary. Use its terms verbatim in issue titles, test names, identifiers and prose; it lists the synonyms to avoid.
- `docs/adr/` — decisions already made. If your work contradicts one, say so explicitly rather than quietly overriding it.
- `.env.example` — every secret the project needs, annotated with the constraint that bites for each one. It is the fastest map of the external dependencies.

Ecosystem facts here are dated and move monthly (UCP releases, handler census, NPCI/Razorpay pilot scope). Re-verify version-dependent claims against primary sources rather than trusting the file's timestamp.

## Architecture invariants

These are the decisions everything else hangs off. Breaking one silently invalidates the project's claims.

- **Authority is server-side, never in the agent.** The agent — ours or a third party's — only ever *proposes*. Every guardrail, scope check and policy decision is deterministic code in the server, because an agent we don't control never runs our client code. **No LLM anywhere in the request path.**
- **Refunds are requests, never executions.** The server records and evaluates a refund request and returns a refusal or an approval requirement; a human executes it in the Razorpay dashboard. We never call Razorpay's refund API. This deliberately keeps us outside the payment aggregator's obligations.
- **Conform to UCP `2026-08-25`, don't invent tool contracts.** Catalog/cart/checkout/order tool names are the standard's. Extensions go under `app.vercel.tillhand.shopping.*` (`suggestions`, `refund_request`). So does our payment handler, since authority binding covers handlers too. `2026-08-25` made namespace authority binding a **MUST**: a capability whose schema host doesn't match its namespace is **silently rejected**, with no error. Business "no" outcomes (out of stock, not found, timeouts) are normal results with `ucp.status: "error"` and `messages[]`, never `isError` or an exception.
- **`Order` (ours, in Neon) and `PaymentIntent` (Razorpay's) are different objects with different lifecycles.** One Order accumulates several Razorpay orders across retries. Collapsing them produces an ambiguous audit trail.
- **The Harness talks to our own MCP server as a real client over HTTP**, not by importing the tool functions — that is what keeps the third-party-agent claim honest. Tools are defined once as plain Python functions; the server and any direct caller wrap that single definition. See `docs/adr/0001`.
- **Payment completion is asynchronous.** UPI has no agent-suppliable token: the handler returns a payment intent (link/QR), the human approves in their bank app, and the order completes on webhook. Webhooks are at-least-once and can arrive out of order, so **state moves forward only**.
- **Vectors live in Neon via pgvector**, not a separate vector store. See `docs/adr/0002`.

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
- Without `TransportSecuritySettings(allowed_hosts=[...])` every request returns **HTTP 421** (localhost-only DNS-rebinding protection is on by default).
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
