# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TillHand

An agentic-commerce layer for a single D2C merchant, built for the Razorpay Buildathon (Track 01): a UCP-conformant MCP server over a Neon catalog, a UPI/Razorpay payment handler, and a server-side authorization layer, plus a thin agent client.

## Repository state

**Pre-code.** The repo currently holds only planning and domain documents — there is no source tree, no dependency manifest, and therefore no build, lint, run, or test commands yet. Do not invent them; when the first code lands, record the real commands here.

Intended stack, from the plan and `.env.example`: Python, the `mcp` SDK over streamable HTTP, Neon Postgres with pgvector, the Razorpay REST API (test mode), Prompt Guard 2 on CPU via `torch`, DeepEval for evals, Docker on Railway.

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
- **Conform to UCP `2026-08-25`, don't invent tool contracts.** Catalog/cart/checkout/order tool names are the standard's. Extensions go under `com.tillhand.*` (bundles/upsell, refund requests) — and `2026-08-25` made namespace authority binding a **MUST**: a capability whose schema host doesn't match its namespace is **silently rejected**, with no error.
- **`Order` (ours, in Neon) and `PaymentIntent` (Razorpay's) are different objects with different lifecycles.** One Order accumulates several Razorpay orders across retries. Collapsing them produces an ambiguous audit trail.
- **The Harness talks to our own MCP server as a real client over HTTP**, not by importing the tool functions — that is what keeps the third-party-agent claim honest. Tools are defined once as plain Python functions; the server and any direct caller wrap that single definition. See `docs/adr/0001`.
- **Payment completion is asynchronous.** UPI has no agent-suppliable token: the handler returns a payment intent (link/QR), the human approves in their bank app, and the order completes on webhook. Webhooks are at-least-once and can arrive out of order, so **state moves forward only**.
- **Vectors live in Neon via pgvector**, not a separate vector store. See `docs/adr/0002`.

## Constraints that will waste your time if you forget them

- **Razorpay test mode: 30 payment links per business, ever.** UPI payment links are unavailable in test mode, and links are browser-only (no headless completion). Mock the Razorpay boundary in the eval suite; spend real links only on hand-run demos.
- **ngrok is blacklisted by Razorpay webhooks**; `zrok` is the recommended tunnel. Local dev cannot otherwise receive webhooks.
- **`mcp` 2.x is a breaking rewrite** and stateless is now native — `stateless_http=True` is a v1 idiom. Pin the SDK line explicitly. MCP code recalled from memory or older tutorials is likely wrong.
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
