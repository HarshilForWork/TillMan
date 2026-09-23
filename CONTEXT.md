# TillHand

An agentic-commerce layer for a D2C merchant: a machine-legible catalog plus a guarded agent harness that lets an AI agent browse and buy on a customer's behalf, within bounds the customer actually agreed to.

## Language

### Commerce

**Order**:
The merchant's own record of what a Customer is buying: line items, quantities, and fulfilment state. Owned by TillHand, stored in Neon. Exists before payment, survives a failed payment, and can be partially refunded.
_Avoid_: Purchase, transaction, cart (a Cart is a distinct earlier stage)

**PaymentIntent**:
The Razorpay-side object representing an attempt to collect money for an Order. An Order references its PaymentIntent; the two have separate lifecycles and an Order may accumulate several over retries.
_Avoid_: Razorpay order, payment, charge

**Cart**:
The mutable set of Variants a Customer has assembled during a Session, before it is committed into an Order.
_Avoid_: Basket, bag

**Product**:
A sellable item in the merchant's catalog, e.g. a moisturiser. Carries no price or stock of its own; those belong to its Variants. Either active or **discontinued**: a discontinued Product can no longer be found or suggested, but still exists so past Orders can refer to it.
_Avoid_: Item, SKU (a SKU labels a Variant, not a Product)

**Option**:
A dimension along which a Product's Variants differ, with its allowed values, e.g. Size: 30ml / 50ml / 100ml. Belongs to the Product.
_Avoid_: Axis, attribute

**Variant**:
A specific purchasable configuration of a Product, defined by one chosen value for each of the Product's Options, and carrying its own price and, optionally, a stock level. A Variant with no stock level is **untracked** and always available. Identified by its id; a SKU is an optional merchant-facing code for it.
_Avoid_: SKU (the SKU is a label, the Variant is the thing)

**Bundle**:
A curated, directed pairing from one Product to another that the Merchant recommends buying together, e.g. cleanser → moisturiser. Always Product-to-Product; the Variant is chosen afterwards.
_Avoid_: Combo, routine, kit

**Suggestion**:
A Product offered to the Customer as an upsell or cross-sell, whatever produced it: a Bundle, or similarity when no Bundle exists.
_Avoid_: Recommendation, upsell (as a noun)

**Customer**:
The person on whose behalf the agent acts, and to whom Orders and memory are scoped.
_Avoid_: User, client, buyer, account

**Merchant**:
A D2C brand whose catalog TillHand exposes. Each Merchant gets its own deployment; no deployment serves more than one.
_Avoid_: Seller, store, vendor, tenant

### Agent

**Harness**:
The deterministic code around the model: it perceives, plans, dispatches tool calls, applies guardrails, logs, and halts. The model decides intent; the Harness decides whether the intent is permitted to happen.
_Avoid_: Orchestrator, agent loop, controller

**Guardrail**:
A check the Harness applies that can block an action. Three layers: input (injection detection on untrusted text), action (deterministic policy on proposed tool calls), output (what may be shown back).
_Avoid_: Rail, filter, policy check

**Session**:
One continuous interaction between a Customer and the agent. Bounds Working Memory and is the unit at which Preference Memory is written.
_Avoid_: Conversation, chat, thread

**Working Memory**:
Current Session state — Cart contents, what has been discussed. Plain structured state, discarded when the Session ends.
_Avoid_: Short-term memory, context

**Episodic Memory**:
What happened in this Customer's past Sessions — previous Orders, sizes bought, complaints. Retrieved and re-injected when a new Session starts.
_Avoid_: History, long-term memory

**Preference Memory**:
Distilled durable facts about a Customer — size, style, budget sensitivity. Written deliberately at the end of a Session, never as a raw transcript dump.
_Avoid_: Profile, preferences, traits
