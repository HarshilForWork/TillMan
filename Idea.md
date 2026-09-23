# TillHand — AI Growth & Agentic Commerce Agent (Razorpay Buildathon — Track 01)

> **Revised 21 September 2026**, after nine research passes and an independent adversarial verification pass (see `updates.md`). Claims corrected where that pass falsified them; corrections are listed in [What changed](#what-changed-from-the-original-plan).

## Problem Statement

Shopping is shifting from "a person browses and clicks checkout" to "a person delegates browsing and buying to an AI agent." This breaks two things for merchants today:

1. **Transactability** — checkout assumes a human is present to click "pay," enter an OTP, etc. An agent acting on someone's behalf needs a way to browse, decide and pay that is conversational and programmatic, but still safely bounded by limits the human actually agreed to.
2. **Discoverability** — merchants are built to be found by human eyes (websites, SEO), not by agents that need clean, structured, machine-readable data about what's for sale, at what price, in what variants, and whether it's in stock.

This matters now because the infrastructure is being built in real time (Google and Shopify's UCP, Google's AP2, OpenAI/Stripe's ACP, Coinbase's x402, NPCI's Unified Agent Protocol for UPI), and Razorpay is already running agent-payment pilots with NPCI.

**The bar we're building to:** every money-moving action must be explainable (why did it do that), bounded (hard limits it cannot exceed), gated (a checkpoint before money actually moves), logged (a full audit trail), and resilient (at least one realistic failure — a declined payment — handled gracefully instead of breaking).

## What's actually novel here

Three things, in order of strength. Everything else in this document is competent plumbing in service of them.

1. **A UPI/Razorpay payment handler for UCP.** UCP assumes the agent supplies a tokenised credential. UPI assumes a human approves in their own bank app. Nobody has bridged them — verified 21 Sep 2026 against a live census of ~68 handlers across ~21,671 storefronts: none for UPI, India, or Razorpay. *This is the most time-sensitive claim in the plan; re-check on demo day.*
2. **Bundle and upsell as an agent-callable capability.** UCP has no cross-sell capability (roadmap only). This is what makes Claim A a revenue story — *"the agent grew basket size"* — rather than a plumbing story, and Track 01 is literally called **AI Growth**.
3. **A server-side authorization layer.** UCP specifies no guardrails at all. Ours is deterministic code the caller cannot bypass, demonstrated on the one surface where merchant-side enforcement is conceptually uncontested: refund *requests*.

## Target Audience / Merchant

A **D2C brand with product variants** — skincare, apparel, or supplements. Products have real structure (SKU, size/shade/quantity, price, stock) and natural bundle relationships (cleanser → moisturiser). Rich enough to need real tool design without the scope exploding into B2B negotiation or service scheduling.

## Our Solution — Two Claims

**Ordered deliberately.** The original plan treated third-party agent discovery as the point and the merchant's own assistant as incidental. Research reversed that: the merchant's own agent is where this produces revenue today.

### Claim A — A merchant's agent can transact safely (build fully, demo end to end)

A customer talks to the merchant's own shopping assistant — on the site or on WhatsApp — browses conversationally, gets a relevant bundle suggestion that raises basket size, and completes a purchase. Safely, explainably, with a full audit trail, over Razorpay test mode and UPI.

No external channel needed. The merchant already has the traffic, and the outcome is measurable as conversion and average order value on traffic they already pay for.

### Claim B — The same server is channel-ready (build the mechanics, demo honestly)

The identical infrastructure conforms to **UCP (Universal Commerce Protocol)** — the standard Google and Shopify ship, live on thousands of storefronts — and publishes a profile at `/.well-known/ucp`. Any UCP-aware client that has our domain can discover our capabilities and transact with no integration written by anyone.

**Stated precisely, because the honest version is narrower than it sounds:**

- **Discovery works, and UCP does define it.** `/.well-known/ucp` is the spec's normative discovery entry point. What does *not* exist is any crawl, registry or index — so **no consumer agent will find our domain unprompted.** Platforms source merchants from feeds and partnerships: Google AI Mode from Merchant Center plus a selection process, ChatGPT from ACP push feeds, Copilot by ingesting UCP as a feed format.
- **No *open, self-serve* consumer channel serves Indian buyers yet.** Google's agentic checkout is live in the US and Australia (Canada and the UK announced); Copilot requires selling to US customers. **Closed pilots do already serve India** — Razorpay and NPCI launched agentic payments on Claude in February 2026 for Zomato, Swiggy and Zepto over UPI Reserve Pay, following an earlier ChatGPT pilot. The rails are arriving; the open surface isn't here yet.
- Entering a live channel later means adding a product feed and REST checkout endpoints on top of what we have. **An integration, not a rewrite.** That is the claim — not that it works automatically.

## What To Implement

### 1. Merchant data layer

- A catalog model in **Neon Postgres**: products, variants (size/shade/qty), stock, price, bundle relationships.
- Roughly 20–50 SKUs of realistic **synthetic** data.
- **pgvector in the same database** for semantic search — embeddings live in the same transaction as the rows they describe, so there is no second store to keep in sync.
- A minimal public storefront serving `/.well-known/ucp` on its own domain and reading the same catalog, so site and agent can never disagree on price or stock.

### 2. UCP-conformant MCP server

**Conform to UCP `2026-08-25` rather than inventing tool contracts.** Two tools originally designed here — `search_catalog` and `get_product` — turned out to be the standard's exact names.

- Catalog: `search_catalog`, `lookup_catalog`, `get_product`.
- Cart: `create_cart`, `get_cart`, `update_cart`, `cancel_cart`.
- Checkout: `create_checkout`, `get_checkout`, `update_checkout`, `complete_checkout`, `cancel_checkout`. Note `update_checkout` is a documented near-no-op.
- Order: `get_order` (read-only in UCP; refunds appear here as signed-negative adjustments).
- **Our extensions, namespaced `com.tillhand.*`:** bundle/upsell suggestions, and refund *requests*. The spec reserves `dev.ucp.*` for the Tech Council; any vendor may publish under its own reverse domain.
- Money as integer minor units plus ISO 4217 — already matches paise.
- `complete_checkout` requires `meta.idempotency-key`.
- **Signing:** UCP says platforms *SHOULD* sign with RFC 9421 HTTP Message Signatures and permits API keys, OAuth or mTLS instead; only **webhooks MUST** be signed. Signing checkout but not catalog reads is therefore our design choice, not a spec mandate.
- MCP-spec errors: `ToolError` for agent-recoverable states, `MCPError` only for malformed calls.
- Catalog also exposed as an MCP **resource**, not only as tools.

> ⚠️ **Verify contracts against `2026-08-25` specifically, not `2026-04-08`.** That release tightened **namespace authority binding into a MUST**: an entity whose schema host does not match its namespace is *silently rejected*. Our `com.tillhand.*` schemas must be hosted on a matching host or platforms will drop them without an error.

### 3. A UPI / Razorpay payment handler for UCP

**The project's headline contribution.** Handlers are vendor-namespaced, so `com.tillhand.razorpay.upi` is legitimate without a spec change.

The shapes don't match: the live handlers (`com.google.pay`, `dev.shopify.card`, `dev.shopify.shop_pay`) all assume the agent supplies a token that `complete_checkout` charges. UPI has no such token — a human approves in their bank app.

So the handler returns a **payment intent** (link or QR), the human approves, and the order completes **asynchronously on webhook**. This mirrors the `continue_url` handoff pattern UCP and Shopify already use when an agent cannot complete a payment itself.

Risks this flow has to handle explicitly: webhook-arrives-before-redirect races; Razorpay auto-refunding an authorised-but-uncaptured payment if the user never returns; at-least-once and out-of-order delivery requiring strictly forward-only state.

### 4. Bundle and upsell

An agent-callable `com.tillhand.*` capability returning relevant cross-sell candidates for the current cart, leaning on preference memory where it helps. Curated bundle relationships plus embedding similarity — no LLM call needed.

This is the growth half of the pitch. The metric is basket size, and it is measurable against a control.

### 5. Server-side authorization layer

**All enforcement lives in the server, not in the agent.** The agent — ours or anyone's — only ever *proposes*. An agent we don't control never runs our client code, so a guardrail in the client protects only the caller that was never the risk.

Everything here is plain deterministic code. No LLM anywhere in the request path.

- **Identity**: the UCP agent profile (`meta.ucp-agent.profile`) identifies the caller; per-caller scopes and audit attribution hang off it.
- **Refunds: request-only, never executed.** An agent may *ask*; it can never cause money to move. The server records the request, evaluates policy, and either refuses or returns an approval requirement — a human executes in the merchant dashboard. **We never call Razorpay's refund API.** Refund execution is the payment aggregator's obligation, not ours, and this keeps us entirely outside it.
- **Purchase consent**: the customer approving in their own bank app with their own PIN is stronger consent than any token we could mint. UPI approval *is* the gate for purchases.
- **Per-customer data isolation**: enforceable only server-side, and tested rather than asserted.
- **Injection screening** with Meta's Prompt Guard 2, in-process on CPU, in both directions — inbound untrusted text, and our own catalog copy on the way out so we are not the injection vector into somebody else's agent.
- **Output checks**: no leaked internal reasoning, no cross-customer data.

The whole surface must be testable with plain HTTP calls and no model in the loop.

### 6. The agent client

A **thin chat loop**, not a state machine that owns authority: list the MCP tools, hand them to the model as function declarations, dispatch, feed results back.

Its one genuinely important job is bridging server and human — surfacing a payment intent or an approval requirement and carrying the result back. It also owns a step limit and retry on transport failures.

Deliberately replaceable: the same endpoint should work from Claude Desktop as a custom connector. That's the point, not an embarrassment.

### 7. Memory

All three layers are **merchant data on the server**, not agent state:

- **Working**: the cart is server state; the transcript belongs to the client.
- **Episodic**: past orders, sizes bought, complaints — retrieved at session start, scoped to one customer by the caller's identity.
- **Preference**: durable facts, derived deterministically from order history where possible rather than inferred by a model.

### 8. Eval harness

DeepEval with trajectory scoring. A trajectory is `tools_called` versus `expected_tools`.

- **`ToolPermissionMetric` for every guardrail scenario** — allowlist/denylist over tool names, no model, no API key, fully deterministic, threshold 1.0.
- `ToolCorrectnessMetric` is deterministic **only if `available_tools` is not passed** — passing it invokes an LLM for optimality. Don't pass it. Its handling of an extra rogue call varies by config, which is exactly why guardrail scenarios use `ToolPermissionMetric` instead of relying on it.
- Judge model is Groq. DeepEval has no native Groq support (`GrokModel` is xAI's Grok, a different thing) — **check the LiteLLM route first**, which may avoid writing a `DeepEvalBaseLLM` subclass.
- `DEEPEVAL_TELEMETRY_OPT_OUT=1`.
- **Mock the Razorpay boundary** for the scenario suite; keep a handful of real runs by hand. Test mode allows only 30 payment links per business, ever, which makes a live happy-path demo fragile.

Scenarios: normal purchase completes · bundle offered and accepted or declined · **over-limit refund request refused** · attempted overspend blocked · prompt injection in product data caught · ambiguous request produces a clarifying question · out-of-stock handled gracefully · payment failure retried once then surfaced clearly.

The refund-refusal scenario is the cheapest and most valuable in the suite: it needs no payment, no UPI, no test link, and it proves the *bounded* and *gated* properties on the surface where they matter most.

### 9. PII handling

- **Payment details never touched.** Razorpay's hosted flows keep card and bank data on their PCI-DSS side; we only ever see order ids, payment link URLs and status.
- **Presidio** redaction before anything reaches the audit log. The original second insertion point — before third-party LLM calls — has largely evaporated, since the server makes no model calls in the request path.
- All eval and demo data synthetic from the start.
- Customer profile and order history encrypted at rest.

### 10. Audit trail

Structured logs for every tool call, every guardrail decision and every outcome, attributed to the calling agent's identity. The server sees every call and every argument, making it the only complete vantage point — and it is what makes "explainable" and "bounded" demonstrable rather than claimed.

### 11. Deployment

Containerised on **Railway** with a real Dockerfile. **Docker is available locally**, so the image is built and run before it is pushed &mdash; the three landmines below are all things a local `docker build` and one `curl` catch in seconds, and a deploy cycle catches slowly.

The database is **Neon in every environment**, including local development. That inherits Neon's idle-pause latency and connection limits during dev, which is the cost of never hitting a works-locally-breaks-on-Neon surprise.

- Without `TransportSecuritySettings` (`allowed_hosts`), every request returns **HTTP 421** — the SDK enables localhost-only DNS-rebinding protection by default.
- Railway has no sticky sessions, so the server must be stateless. Note `stateless_http=True` is a v1 idiom; the 2026-07-28 spec and SDK v2 make stateless the native default. **Pin the SDK line explicitly** rather than letting a fresh build resolve `mcp==2.x` by accident.
- `torch` must install from the CPU index, or the default Linux wheel pulls the CUDA stack for a GPU that will never exist.

Razorpay **test mode** keys throughout — no real money ever moves.

## Build Order

1. Catalog data layer on Neon with pgvector, plus seed data.
2. UCP-conformant MCP server: catalog tools first, then cart and checkout.
3. `/.well-known/ucp` profile and the demo storefront.
4. The UPI/Razorpay payment handler — one purchase end to end.
5. Server-side authorization: scopes, refund-request policy, data isolation, injection screening.
6. Bundle and upsell.
7. The thin agent client, plus the same endpoint working from Claude Desktop.
8. Memory layers.
9. Eval harness against what now exists; run and fix regressions.
10. Cloud deployment, logging and tracing.

**Prioritise Claim A end-to-end over Claim B breadth.** A ten-phase build is ambitious for a hackathon window; the bar the track is judged on maps onto Claim A.

## Explicit Non-Goals

- **We never execute a refund.** An agent may request one; a human executes it in the dashboard. Refund execution obligations — including RBI's requirement that refunds return to the original payment method — bind the **payment aggregator**, not us, and we intend to stay outside that entirely. Razorpay's own MCP server already ships `create_refund` for merchants who want ops automation; that is a different product for a different caller.
- **We do not use Razorpay's MCP server inside our own.** It is merchant-side API automation (35+ tools at `mcp.razorpay.com`), not a UCP payment handler or an agentic-checkout protocol. Our server is deterministic code and should call the Razorpay REST API directly; MCP belongs between a *model* and tools, not between two pieces of our own code. It is genuinely useful in the *development* loop.
- **We are not using NeMo Guardrails as the tool-call gate.** Its IORails path (v0.23+) *does* suppress blocked tool calls, but it is schema/structural validation only — it cannot express a policy like a spend limit — supports only the OpenAI Chat Completions wire format (not Gemini), and is opt-in and experimental. The path that *can* run arbitrary policy returns the blocked call to the caller anyway. More fundamentally: a third-party agent never runs our NeMo in-loop, so enforcement has to be server-side regardless.
- **We are not claiming a consumer AI agent will discover us.** No crawl or registry exists, and no open consumer channel serves Indian buyers yet.
- **We are not relying on `llms.txt` or schema.org** as the discovery mechanism — 97% of published `llms.txt` files receive zero requests in a month, and neither UCP nor ACP builds on schema.org. We serve `llms.txt` as a cheap redundant hint only.
- We are not building decoy merchants, not multi-tenant, and not touching any payment credentials but our own.
- We do not rely on the model to self-police. All safety-critical checks are deterministic code.
- The submission video is downstream of the destination, not a workstream competing with it.

## Known Constraints

- **Namespace authority binding is a MUST in `2026-08-25`** — schema host must match namespace, or the capability is silently rejected.
- **Razorpay test mode: 30 payment links per business, ever.** Docs don't say lifetime or rolling.
- **UPI payment links are unavailable in test mode.**
- **ngrok is blacklisted** for Razorpay webhooks; `zrok` is their recommendation. Local dev cannot receive webhooks.
- **Payment links are browser-only.** No headless completion; the S2S API is reportedly gated behind a support request and PCI-DSS certification — *confirm in the dashboard before relying on this in the narrative.*
- **`receipt` is an idempotency key** — Razorpay's docs state a second create with the same value is rejected — so it must vary per attempt.
- **One Razorpay order per payment attempt.** Our Order accumulates several; separate objects, separate lifecycles.
- **Razorpay's Python SDK is sync-only** (verified by installing it and inspecting: no async paths, `iscoroutinefunction(Client.request)` is False) — every call needs `asyncio.to_thread` — and it collapses all HTTP errors to a bare string.
- **Webhooks are at-least-once and can arrive out of order** — state must only move forward.
- **Prompt Guard 2** ships under the **Llama 4 Community License** (free but gated, with an EU-domicile carve-out), not MIT. 86M is multilingual including Hindi; **512-token context**, so long catalog copy must be chunked, and benign product text can trigger false positives.
- **`mcp` 2.x is a breaking rewrite.** Any MCP code from memory or a tutorial predating it is likely wrong.
- **UCP adoption is real but concentrated** — thousands of storefronts, roughly 99% of them Shopify, auto-enabled rather than chosen.

## What changed from the original plan

| Originally | Now | Why |
| --- | --- | --- |
| Third-party discovery was the headline claim | The merchant's own agent is | No open consumer channel serves Indian buyers; this is where revenue exists today |
| Design our own seven MCP tools | Conform to UCP `2026-08-25` | The standard already defines them, live on thousands of stores |
| `create_order` → `create_payment_link` | `create_cart` → `create_checkout` → `complete_checkout` | UCP has no payment-link concept; payment is a declared handler |
| Guardrails in the agent harness | Guardrails in the server | A third-party agent never runs our harness, so client-side enforcement protected nobody |
| The harness was "the core engineering work" | The guarded server is | Authority moved; the client became a thin, replaceable loop |
| `issue_refund` executed refunds | **Refund requests only; a human executes** | Removes the aggregator obligation, the payment dependency, and the overlap with Razorpay's own MCP — while keeping the guardrail demo |
| Bundles were a minor tool | **A headline differentiator** | UCP has no cross-sell capability, and it is what makes Claim A a growth story |
| Qdrant for catalog vectors | pgvector in Neon | 50 SKUs doesn't justify a second stateful service and a sync problem |
| `llms.txt` + JSON-LD make us discoverable | `/.well-known/ucp` does | Nobody fetches `llms.txt`; neither standard uses schema.org |
| "Order" meant one thing | **Order** (ours) and **PaymentIntent** (Razorpay's) | Different lifecycles; collapsing them produces an ambiguous audit trail |

### Corrected after adversarial review

| Claim as written | Correction |
| --- | --- |
| "UCP defines no discovery mechanism at all" | False — `/.well-known/ucp` **is** the spec's normative entry point. The true point is that no *crawl or registry* exists |
| "UCP covers neither refunds" | False — refunds are modelled as order adjustments. UCP lacks an *agent-callable refund tool* |
| "NeMo cannot gate tool calls" | Stale — IORails does suppress blocked calls, but cannot express our policy and excludes Gemini |
| "No consumer channel serves Indian buyers" | Needs the qualifier *open/self-serve* — closed Razorpay–NPCI pilots do serve India |
| "Checkout requires RFC 9421" | Overstated — the spec says SHOULD; only webhooks MUST be signed |
| Google checkout is US/CA/AU | Currently **US and Australia**; Canada and the UK are announced |

Full verification record, including what could not be confirmed from primary sources, is in `updates.md`.
