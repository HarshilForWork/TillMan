# TillHand — Independent Adversarial Verification Pass (as of 21 September 2026)

*A fresh, primary-source re-check of every load-bearing claim in the TillHand build plan. Verdicts: VERIFIED / PARTIALLY VERIFIED / UNVERIFIABLE / CONTRADICTED / CHANGED SINCE. Where no primary source was found, that is stated plainly rather than inferred.*

## TL;DR
- **The plan's spine holds.** UCP version `2026-08-25` is real and current; the catalog/cart/checkout/order tool names are correct; the core Razorpay test-mode constraints check out; `get_order` is read-only; money is integer minor units + ISO 4217; and a UPI/Razorpay UCP payment handler is a **genuinely unclaimed gap** — verified against a live census of ~68 handlers across ~21,671 storefronts, none for UPI/India/Razorpay. The core architecture is defensible.
- **Three findings would embarrass the team in a live demo if unaddressed:** (1) **Razorpay ships an official, public, self-serve MCP server** (`mcp.razorpay.com`, 35+ tools including refunds and payment links) — a judge will ask why you hand-rolled the Razorpay boundary; (2) **NeMo Guardrails now HAS model-free tool-calling rails (IORails, v0.23.0) that DO suppress blocked tool calls** — the plan's stated reason for rejecting it is factually stale; (3) **the claim "UCP defines no discovery mechanism at all" is false** — the spec makes `/.well-known/ucp` the normative discovery entry point.
- **Indian-buyer availability moved.** "No consumer channel serves Indian buyers" is only true for *open/self-serve* UCP surfaces. Closed consumer pilots via **Razorpay–NPCI now do serve Indian buyers** — on Claude (Zomato/Swiggy/Zepto, launched 20 Feb 2026) and earlier on ChatGPT (BigBasket). Claim B needs that qualifier.

---

## A. Claim-by-claim verification table

### A1. UCP core protocol

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| UCP version string `2026-08-25` exists and is current | **VERIFIED** | `github.com/Universal-Commerce-Protocol/ucp/releases/tag/v2026-08-25` (released 25 Aug 2026); Shopify changelog "Shopify storefronts now support UCP 2026-08-25" | Latest published version. Prior: 2026-04-08, 2026-01-23, 2026-01-11. |
| Tool names `search_catalog`, `lookup_catalog`, `get_product` are the standard's | **VERIFIED** | `ucp.dev/draft/specification/catalog/lookup/`; Shopify `shopify.dev/docs/agents/catalog/global-catalog` | Names match verbatim. |
| `lookup_catalog` + `get_product` are a **required pair** | **PARTIALLY VERIFIED** | UCP catalog spec | Both are defined and distinct ("Use lookup_catalog when you have identifiers…; get_product when a product has been identified"). The "must ship both together" framing is not confirmed as spec-mandated language. |
| Cart tools `create_cart/get_cart/update_cart/cancel_cart`; checkout `create/get/update/complete/cancel_checkout` | **VERIFIED** | UCP checkout spec; Glama/Facet UCP connector inventory | Verb_noun set confirmed. Note: `update_checkout` is a documented near-no-op ("does not perform an update"). |
| `get_order` is read-only in UCP | **VERIFIED** | `ucp.dev/.../order/`; `ucp.dev/2026-04-08/specification/order-mcp/` | Agents do not write order state; `order.attribution` is explicitly read-only. |
| Spec forbids non-owners using `dev.ucp.*`; vendors use own reverse-domain namespace | **VERIFIED** | `ucp.dev/documentation/core-concepts/`: "The `dev.ucp.*` namespace is reserved exclusively for capabilities governed by the UCP Tech Council… Any vendor can define and publish capabilities under their own domain — org.acme.* — without UCP maintainer approval." | `com.tillhand.*` is legitimate. |
| Money = integer minor units + ISO 4217 | **VERIFIED** | UCP catalog overview: "Price values include both amount (in minor currency units) and currency code" | Matches paise. |
| `complete_checkout` requires `meta.idempotency-key` | **VERIFIED** | UCP checkout spec; Google "Under the Hood" example carries `idempotency-key` header | Idempotency mandated. |
| Checkout requires RFC 9421 signatures but catalog reads do not | **CONTRADICTED (as a hard requirement)** | `ucp.dev/specification/signatures/`: "Platforms **SHOULD** sign all requests when using HTTP Message Signatures. Alternative authentication mechanisms (API keys, OAuth, mTLS) may be used instead. Webhooks… **MUST** be signed." | Signing checkout-but-not-catalog is a *permitted design choice*, not a spec mandate. Only webhooks are a MUST. Reframe accordingly. |
| Agent profile field `meta.ucp-agent.profile` | **VERIFIED** | `ucp.dev/2026-04-08/specification/order-mcp/` example: `"meta": { "ucp-agent": { "profile": "https://platform.example/.well-known/ucp" } }`; `UCP-Agent` header carries the profile URL | Correct. |
| Spec says platforms "MAY" fetch `/.well-known/ucp` and **defines no discovery mechanism at all** | **CONTRADICTED** | `ucp.dev/2026-04-08/specification/overview/`: "Businesses publish their profile at /.well-known/ucp **as the discovery entry point** — platforms fetch it"; `glippy.dev` and Google/Shopify docs "both treat /.well-known/ucp as the entry point" | The DEFENSIBLE narrower point: there is no crawl/registry, so **no consumer agent auto-discovers your domain unprompted**. But UCP *does* define discovery. Rewrite this sentence — it is trivially falsified by reading the spec. |

### A2. Payment handlers

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| `com.google.pay` is a live handler | **VERIFIED** | `developers.google.com/merchant/ucp/guides/google-pay-payment-handler`: "Handler Name: com.google.pay" | — |
| `dev.shopify.card` is a live handler | **VERIFIED** | UCP core concepts names `com.google.pay`, `dev.shopify.shop_pay`; `dev.shopify.card` appears in the live handler census | Shopify's Shop Pay handler is `com.shopify.shop_pay` / `dev.shopify.shop_pay`; card handler `dev.shopify.card` is also live. |
| No UPI/India/Razorpay handler defined by anyone | **VERIFIED (gap open)** | `ucpchecker.com/payment-handlers` — live census of **68 handlers across 21,671 storefronts**; full enumerated list contains none for UPI/India/Razorpay | Handlers present: Google Pay, Shopify Card/Shop Pay, Stripe (multiple), Klarna, Adyen, x402, AP2, COD variants, mocks — but no `com.razorpay.*` / `upi` / `india` namespace. **A `com.tillhand.razorpay.upi` handler would be first.** *Caveat: an unmerged draft PR in the official repo cannot be 100% excluded; none surfaced.* |
| Handler vendor-namespacing (`com.tillhand.razorpay.upi`) is legitimate without a spec change | **VERIFIED** | `shopify.engineering/UCP`: "each provider… publishes their own handler specification"; UCP core concepts | Correct. |
| UCP covers **neither** refunds **nor** bundle/upsell suggestions | **PARTIALLY CONTRADICTED** | `ucp.dev/.../order/`: adjustments "typically money movements like refund, return, credit… Quantities and amounts are signed—negative for reductions (returns, refunds)"; proposed extension `dev.ucp.common.payment.processing` (Issue #820) | **Refunds ARE modeled** — as post-order order adjustments (`type: refund`, signed negative amounts). What UCP lacks is an *agent-callable refund tool*. Bundle/upsell: no UCP capability found — that part stands. Reframe: "an agent-callable refund tool, which UCP lacks," not "UCP covers neither refunds." |
| Shape mismatch: live handlers assume agent supplies token → `complete_checkout` charges it; UPI has no token, human approves in bank app | **VERIFIED** | Google Pay handler (tokenization); Shop Pay handler ("single-use Shop Pay token"); UCP core concepts ("Credentials flow platform → business only") | The async **payment-intent (link/QR) + webhook completion** shape is the correct workaround and mirrors the merchant-handoff `continue_url`/`permalink_url` pattern in Shopify's/UCP's own checkout-handoff design. |

### A3. Geographic availability (21 Sep 2026)

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| Google UCP/AI Mode agentic checkout is US/CA/AU only | **CHANGED SINCE / PARTIALLY** | `support.google.com/googlepay/answer/16421147`: "currently only available in the US and Australia"; `blog.google` GML: CA/AU "in the coming months and later to the U.K." | Current live surface is **US + Australia**; Canada/UK announced, not shipped. **Not India.** |
| Copilot requires selling to US customers | **VERIFIED** | Shopify Help "Selling on Microsoft Copilot": "Your store must sell to customers in the United States"; Microsoft Advertising: "Only English-language merchants who sell to US buyers are eligible at this time (supporting USD)" | Copilot Checkout is US-only, on Copilot.com. |
| No consumer channel currently serves Indian buyers | **PARTIALLY CONTRADICTED** | Razorpay blog "Agentic Payments & NPCI"; Analytics India Magazine | True for *open/self-serve UCP surfaces*. **False for closed pilots:** Razorpay + NPCI launched Agentic Payments on **Claude at the India AI Impact Summit, New Delhi, 20 Feb 2026, for Zomato, Swiggy and Zepto, powered by UPI Reserve Pay, "currently in a pilot phase with a select group of users."** Earlier ChatGPT/OpenAI pilot (Oct 2025, BigBasket) also served Indian buyers. Add the qualifier "no *open* consumer channel." |
| NPCI Unified Agent Protocol (UAP) status | **VERIFIED (in development)** | Reuters (reported 1 Sep 2026, three sources), via Inc42/cio.inc | NPCI built UAP to let agents pay on UPI without per-transaction approval; expected at **Global Fintech Fest, Mumbai, 8–11 Sep 2026**; builds on **UPI Circle (₹15,000/month delegated cap)** and **Reserve Pay (₹10,000 block, up to 90 days)**. "NPCI has not publicly released its specifications or liability model." RBI approval pending. |
| Razorpay is piloting agent-driven payments with NPCI | **VERIFIED** | Razorpay blog; Medianama | Claude pilot Feb 2026; ChatGPT/OpenAI pilot Oct 2025. Both pilot-stage, closed user group. |
| Context standards AP2 / ACP / UCP / x402 / NPCI UAP being built in real time | **VERIFIED** | Google, Shopify, OpenAI/Stripe, Coinbase announcements | Note governance moves: **AP2 now stewarded by the FIDO Alliance; A2A transport by the Linux Foundation.** |

### A4. Razorpay constraints

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| 30 payment links per business in test mode; docs don't say lifetime vs rolling | **VERIFIED** | `razorpay.com/docs/api/payments/payment-links/create-standard/`: "In test mode, you can create up to 30 Payment Links per business. If you need to create more than 30… contact Razorpay Support." | Docs do not state lifetime vs rolling — confirmed. |
| UPI payment links unavailable in test mode | **VERIFIED** | Razorpay create-UPI docs error: "UPI Payment Links is not supported in Test Mode. Please experience the product in Live Mode." | — |
| ngrok blacklisted; zrok recommended | **VERIFIED** | `razorpay.com/docs/webhooks/validate-test/`: "Due to security restrictions, many common tunneling services are blacklisted. You can handle this by creating a tunnel to your localhost using zrok." | — |
| Payment links browser-only; S2S API gated behind PCI-DSS + support request | **UNVERIFIABLE** | — | No primary source found this pass. Verify in dashboard before relying on it in the narrative. |
| `receipt` is an idempotency key that must vary per attempt | **UNVERIFIABLE** | — | No primary source found this pass. |
| One Razorpay order per payment attempt (separate objects/lifecycles) | **UNVERIFIABLE** | — | No primary source found this pass. |
| Python SDK sync-only; collapses HTTP errors to a bare string | **UNVERIFIABLE** | — | No primary source found this pass. Plausible but confirm against the SDK. |
| Webhooks at-least-once and can arrive out of order → forward-only state | **PARTIALLY VERIFIED** | Razorpay webhook docs; Zoho integration KB (authorised-but-uncaptured, late-authorisation cases) | Consistent with documented behavior and general webhook semantics; not directly quoted from a Razorpay reliability-guarantee page this pass. Design conclusion (forward-only state) is sound. |
| **MISSING:** Razorpay ships an official public MCP server | **NEW FINDING** | Razorpay blog "Razorpay Remote MCP 2.0": "With over 35 specialized tools covering payments, orders, refunds, settlements, and more, it stands as the most comprehensive payment MCP server available in India." `github.com/razorpay/razorpay-mcp-server` (MIT); hosted at `mcp.razorpay.com/mcp` | Tools include `create_refund`, `create_payment_link`, `create_qr_code`, `capture_payment`. **This is a merchant-API automation wrapper, not a UCP/agentic-checkout product** — but the plan never mentions it, and a judge will. |

### A5. DeepEval

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| `ToolPermissionMetric` exists; deterministic; LLM-free | **VERIFIED** | `deepeval.com/docs/metrics-tool-permission`: "compares called tool names against your allowlist/denylist — no model, no API key, zero token cost, fully deterministic"; added #2826 | Threshold defaults to 1.0 (any unauthorized call fails). Exactly the right tool for guardrail scenarios. |
| `ToolCorrectnessMetric` deterministic & LLM-free | **PARTIALLY VERIFIED** | `deepeval.com/docs/metrics-tool-correctness` | Deterministic **only if `available_tools` is NOT passed**. Passing it "also uses an LLM to find whether the tools_called were the most optimal… final score is the minimum of both." **Do not pass `available_tools`.** |
| ToolCorrectnessMetric scores 1.0 for all-right-calls-PLUS-a-rogue-payment-call | **PARTIALLY VERIFIED / DUBIOUS** | Formula = correct tools ÷ total tools called | Under default config an extra rogue call *lowers* the score below 1.0. The "1.0" outcome depends on `should_exact_match`/param config. **The architectural conclusion is still correct** — use `ToolPermissionMetric` for every guardrail scenario; soften the "scores 1.0" wording. |
| DeepEval lacks native Groq; `GrokModel` is xAI's Grok (a different thing) | **PARTIALLY VERIFIED** | DeepEval docs list OpenAI/Azure/Ollama/Anthropic/Gemini/LiteLLM judges + `DeepEvalBaseLLM` custom path | A `DeepEvalBaseLLM` subclass is the documented custom route. **Note: Groq is reachable via LiteLLM**, so a full subclass may be avoidable — worth checking before writing one. |
| `DEEPEVAL_TELEMETRY_OPT_OUT=1` is the correct variable | **PARTIALLY VERIFIED** | Widely used in DeepEval docs/community | Not directly confirmed against source this pass; name is correct in common usage. |
| 4 of 7 scenarios need no LLM judge | **VERIFIED (directionally)** | Above | `ToolCorrectnessMetric` (no `available_tools`) + `ToolPermissionMetric` are both deterministic/free. |

### A6. NeMo Guardrails

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| A blocked tool call still appears in `result["tool_calls"]` with no block flag in NVIDIA's own tests | **CHANGED SINCE / CONTRADICTED (current versions)** | NVIDIA NeMo Guardrails **v0.23.0** Tool Calling docs: "When the rail blocks, IORails **emits a guardrails_violation error payload and suppresses the tool-call chunk, so a consumer never receives a tool call after a block.**" Tool rails are "local structural/schema validators… requires_model is therefore False." | NeMo now has **model-free tool-calling rails (IORails)** that DO block. **Caveats:** IORails "supports only the OpenAI Chat Completions wire format (the openai and nim engines)" — not Anthropic/Gemini/Bedrock — and is **opt-in and experimental** (must be explicitly enabled). Your deterministic server-side gate is still the right call (a third-party agent never runs your NeMo in-loop; IORails wire-format limit), but **update the stated justification — the "NeMo cannot gate tool calls" premise is outdated.** |

### A7. Railway / deployment

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| Without `TransportSecuritySettings`, every request returns HTTP 421 | **VERIFIED** | `github.com/modelcontextprotocol/python-sdk` issues #1798, #3437; Azure ACA PR #324: "The MCP SDK enables localhost-only DNS rebinding protection by default… requests through an ACA FQDN… were rejected with 421 Invalid Host header." | Real. Fix: set `allowed_hosts` or disable DNS-rebinding protection (which also drops Origin validation — narrow it). |
| `stateless_http=True` required (Railway has no sticky sessions) | **VERIFIED (conceptually)** | Railway docs "Build and Deploy Your Own MCP Server": "requests are stateless… any replica can serve any request" | **Caveat:** MCP spec 2026-07-28 / SDK v2 makes stateless the **native default**; `stateless_http=True` is a v1 FastMCP idiom. Confirm which SDK line you target. |
| torch must install from CPU index or the default Linux wheel drags in ~16 CUDA packages | **PARTIALLY VERIFIED** | Well-known packaging behavior | Not quantified against a primary source this pass; the CPU-index recommendation is correct practice. |

### A8. Prompt Guard 2

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| Sizes 86M and 22M; CPU-viable | **VERIFIED** | Meta model cards (HF `meta-llama/Llama-Prompt-Guard-2-86M` / `-22M`); Medium guide (~19ms 22M, ~92ms 86M, ~350MB) | 86M multilingual incl. Hindi; 22M English-focused. 512-token context max. |
| Appropriately licensed for this use | **CORRECTION** | Model card | Base models (mDeBERTa/DeBERTa) are MIT (Microsoft), **but Llama Prompt Guard 2 itself ships under the Llama 4 Community License (free, gated), with an EU-domicile carve-out** — **not MIT.** Usable, but fix the "MIT" statement. |
| Bidirectional screening (inbound + outbound catalog copy) is sensible | **REASONABLE** | Model card | **MISSING RISK:** 512-token limit → long catalog copy must be chunked; benign product text can trigger false positives. |

### A9. mcp Python SDK

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| mcp 2.x is a breaking rewrite; recalled/tutorial code is wrong | **VERIFIED** | `blog.modelcontextprotocol.io`; DEV/Real Python roundups; releasebot | Stable **v2.0.0 shipped ~28 Jul 2026** for the 2026-07-28 stateless spec. `FastMCP → MCPServer`; `AnyUrl → str`; snake_case attrs; low-level rewrite of `Server(...)`. `pip install mcp` now resolves to 2.x; pin `mcp>=1.28,<2` if not migrated. **Any MCP code from memory/tutorials predating this is likely wrong** — claim stands strongly. |

### A10. RBI

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| Refunds must go to original payment method unless customer agrees otherwise | **VERIFIED (real obligation)** | Lexology / Enterslice on RBI PA guidelines: "All refunds made by the Payment Aggregator must be to the original payment method unless specifically agreed by the customer." | — |
| Citation "RBI Annexure-1 §2.4" | **UNVERIFIABLE (as written)** | RBI "Master Direction on Regulation of Payment Aggregators," 15 Sep 2025 (`fidcindia.org.in` PDF), superseding 2020/2021 PA guidelines | The obligation is real but the exact clause "Annexure-1 §2.4" could not be confirmed and may be stale post-consolidation. **Critical nuance: this binds the Payment Aggregator (Razorpay), not TillHand — TillHand is not the PA.** Data localisation (India-only storage of payment data) also applies to the PA. |

### A11. Other non-goals

| Claim | Verdict | Primary source | Correction / note |
|---|---|---|---|
| 97% of published llms.txt files receive zero requests in a month | **VERIFIED** | Ahrefs "We Analyzed 137K Sites": study of all **137,210 domains** with May 2026 traffic; **97% of valid llms.txt files received zero requests**; AI retrieval bots ~1.1%. Ahrefs caveat: "treat the 28% adoption figure as an upper bound" (customers skew technical) | Methodology sound; serving llms.txt as a cheap redundant hint is defensible. |
| Neither UCP nor ACP builds on schema.org | **VERIFIED (directionally)** | UCP spec (JSON Schema 2020-12, RFC 9421); ACP docs | Confirmed. |
| UCP "live today on thousands of storefronts" | **VERIFIED (arguably understated)** | `universalcommerceprotocol.fr` (8,000+ by mid-June 2026); `ucpchecker.com` (~16,657 verified merchants; 10,493 on 2026-08-25) | **But ~99% are Shopify** (auto-enabled). Adoption is real yet heavily concentrated — say "thousands, ~99% Shopify" to preempt a skeptic. |
| NeMo Guardrails not usable as the tool-call gate | **CONTRADICTED** | See A6 | Stale — IORails exists. |
| Not multi-tenant / not touching others' payment credentials | **SOUND** | — | Reasoning (OAuth account-wide refund authority, RBI questions) is reasonable. |

---

## B. What the plan is MISSING or UNDER-WEIGHTED (be genuinely critical)

1. **Razorpay's official MCP server is the single biggest omission.** `mcp.razorpay.com` exposes 35+ tools including `create_refund` and `create_payment_link` — "the most comprehensive payment MCP server available in India" (Razorpay's own words). A skeptical judge *will* ask why you hand-rolled the Razorpay boundary with `asyncio.to_thread`. **Have the answer ready:** it is merchant-side API automation, not a UCP payment handler or agentic-checkout protocol; it does not provide the server-side authorization layer you need; and running two MCP servers is awkward. Acknowledge it on a slide.

2. **NeMo Guardrails moved.** It now has model-free tool-calling rails (IORails) that suppress blocked tool calls. Your "NeMo cannot gate tool calls" non-goal is factually outdated. Keep the deterministic server-side gate, but justify it correctly: a third-party agent never runs your NeMo in-loop, and IORails only supports the OpenAI Chat Completions wire format.

3. **UCP 2026-08-25 shifted toward payments and tightened namespaces.** The release added instrument requirements/credential splitting, payment terms/schedules, split payments, delegated IdP, and **migrated payment extensions to `dev.ucp.common.payment.*`**. Critically, it made **namespace authority binding a MUST that silently rejects any entity whose schema host does not match its namespace.** This can **silently break `com.tillhand.*`** if your schemas are not hosted on a matching host. Re-verify all tool contracts against **2026-08-25 specifically**, not 2026-04-08.

4. **MCP went stateless natively (2026-07-28 / SDK v2).** `stateless_http=True` is a v1 idiom. On v2 the construction (`MCPServer`) and stateless semantics differ. Decide and pin your SDK line explicitly to avoid a fresh build silently pulling `mcp==2.x`.

5. **Indian-buyer availability changed** (see A3). Claim B's blanket "no consumer channel serves Indian buyers" is contradicted by the Razorpay–NPCI Claude/ChatGPT pilots. Reword to "no *open/self-serve* UCP consumer surface serves Indian buyers yet" — and cite the pilots as evidence the rails are arriving.

6. **UPI-async-webhook checkout risks the plan hasn't fully named:** (a) webhook-arrives-before-redirect races; (b) **auto-refund of authorized-but-uncaptured payments if the user never returns to the payment screen** (documented Razorpay behavior); (c) at-least-once/out-of-order delivery requiring strictly forward-only state; (d) the **30-link test-mode cap makes a live happy-path demo fragile** — mock the boundary, keep only a handful of real runs.

7. **Prompt Guard 2 licensing** (Llama 4 Community License, not MIT), EU restriction, and the **512-token limit** for outbound catalog screening (chunk long copy; expect false positives).

8. **Claim A / Claim B split — the skeptic's attack.** A judge can note that Claim B's "channel-ready" server is **unreachable by any consumer agent today**, and that "discovery once you have the domain" is trivially true of any HTTP endpoint. **Lead with Claim A's measurable conversion story on traffic the merchant already pays for**; frame Claim B honestly as future-optionality ("an integration, not a rewrite"), and prove it by pointing a real UCP client (that works against live merchants) at your `/.well-known/ucp`.

9. **Is the "actual contribution" still unclaimed?** Yes as of 21 Sep 2026 — verified against the live handler census (no UPI/India/Razorpay handler) and the spec. But this is the most time-sensitive claim in the whole plan: if Razorpay or NPCI publishes a UCP handler, or UAP ships a public API, your differentiation shrinks to the authorization layer. Re-check on demo day.

10. **Buildathon Track 01 fit (context).** Razorpay's Buildathon page frames Track 01 as "AI Growth & Agentic Commerce — build an agent that grows revenue for a merchant on Razorpay test-mode APIs, or that makes a merchant transactable by an AI buyer end to end," with the bar: "Every money action explainable, bounded and gated. Show the audit trail and one failure handled gracefully." TillHand's Claim A + authorization/audit design maps *directly* onto this bar — lean into it. **A ten-phase build is ambitious for a hackathon window; prioritize Claim A end-to-end over Claim B breadth.**

---

## C. Highest-priority corrections (ranked by severity)

1. **Razorpay ships an official MCP server (35+ tools, incl. refunds/payment links).** Address proactively or get blindsided in Q&A. *(Build-embarrassing.)*
2. **NeMo Guardrails now blocks tool calls (IORails, v0.23.0).** The stated non-goal is outdated — fix the justification. *(Credibility-embarrassing.)*
3. **"UCP defines no discovery mechanism" is false** — the spec makes `/.well-known/ucp` the normative entry point. Trivially falsifiable by a judge who has read the spec. *(Credibility-embarrassing.)*
4. **"UCP covers neither refunds" is false** — refunds are modeled as order adjustments. Reframe to "no agent-callable refund tool." *(Credibility.)*
5. **RFC 9421 "required on checkout" overstates a SHOULD** (only webhooks are a MUST). *(Correctness.)*
6. **Prompt Guard 2 is Llama-licensed, not MIT**, with an EU carve-out and a 512-token limit on outbound screening. *(Correctness / licensing.)*
7. **"No consumer channel serves Indian buyers"** — contradicted by the closed Claude/ChatGPT Razorpay–NPCI pilots; add the "open/self-serve" qualifier. *(Correctness.)*
8. **Verify against 2026-08-25 (not 2026-04-08)** and confirm `com.tillhand.*` schema hosts satisfy namespace authority binding — or platforms silently reject you. *(Build-breaking, silent.)*
9. **Several Razorpay constraints are UNVERIFIABLE from primary sources this pass** (receipt-as-idempotency-key, order-per-attempt, sync-only SDK, S2S gating behind PCI-DSS). Confirm in the dashboard/SDK before relying on them in the narrative. *(Correctness.)*

---

## D. Caveats
- Where marked **UNVERIFIABLE**, no primary source was found in this pass — treat as "not confirmed," **not** "false."
- UCP, MCP, and the consumer-agent surfaces are changing on a roughly monthly cadence. **Every version-dependent claim here should be re-checked against the exact dated spec on demo day**, especially the handler census (is a UPI handler still unclaimed?), NPCI UAP status (public API yet?), and Google/Copilot geographic scope.
- Some ecosystem figures (store counts, the 68-handler census) come from third-party trackers (`ucpchecker.com`), not the UCP maintainers, and should be treated as directional.
- The RBI clause citation and the finer Razorpay operational limits warrant direct confirmation against the RBI Master Direction PDF (15 Sep 2025) and the Razorpay dashboard/SDK respectively before being stated as fact in a submission.