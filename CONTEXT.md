# TillHand

A product that gives each D2C Merchant its own agent-ready store: a machine-legible catalog plus guarded, server-side authority that lets an AI agent browse and buy on a Customer's behalf, within bounds the Customer actually agreed to.

## Language

### Commerce

**Order**:
The Merchant's permanent record of a **paid** purchase: line items, prices and totals frozen from its Checkout, fulfilment events, and adjustments such as refunds. It's created when a payment is captured, so an Order always means "paid", as in UCP. It's never rewritten, only appended to. It may carry the flags `late_payment`, `oversold` or `duplicate_payment` (a second capture for the same Checkout, which never makes a second Order) for the Merchant to review.
_Avoid_: Purchase, transaction, cart (a Cart is a distinct earlier stage)

**Checkout**:
The purchase being set up and paid for, as in UCP: statuses `incomplete`, `ready_for_complete`, `complete_in_progress`, `completed` and `canceled`. It freezes prices once completion starts, owns every PaymentIntent made for it, and survives failed attempts. It becomes `completed` when one attempt is captured and its Order is created. It lives 6 hours by default.
_Avoid_: Order (an Order exists only once paid), payment session, basket

**PaymentIntent**:
One attempt to collect money for a Checkout, mirrored one-to-one by a Razorpay order whose `receipt` is `{checkout}-{attempt}`. A Checkout may accumulate several over retries. Its states move forward only, and `captured` beats every other state, because UPI can authorise late. The human pays it on its Pay page.
_Avoid_: Razorpay order, payment, charge, attempt (alone)

**Pay page**:
The page on the Merchant's own deployment (`/pay/{payment_intent}`) where a human pays one PaymentIntent, through Razorpay's checkout widget: a test UPI id in test mode, a QR code or UPI app live. Its URL is what `complete_checkout`'s Action and `continue_url` point at. It refuses to open once its attempt is superseded or its approval window (15 min, shrinking to the Checkout's remaining time) has ended.
_Avoid_: Payment link (that's Razorpay's own product, our fallback), checkout page

**Hold**:
Stock set aside for one PaymentIntent while its payment is in progress, so two Customers can't both pay for the last unit. Created at `complete_checkout` under a row lock lasting milliseconds. It expires with its PaymentIntent's approval window, plus 2 minutes, and becomes the real stock decrement when payment is captured. An expired Hold simply stops counting, so no clean-up job is needed.
_Avoid_: Reservation, lock (a lock lasts milliseconds; a Hold lasts minutes)

**Cart**:
The mutable set of Variants a Customer has assembled, before a Checkout is made from it. Always shows live prices. It lives 7 days by default and is cleared when its Checkout completes.
_Avoid_: Basket, bag

**Product**:
A sellable item in the merchant's catalog, e.g. a moisturiser. Carries no price or stock of its own; those belong to its Variants. Either active or **discontinued**: a discontinued Product can no longer be found or suggested, but still exists so past Orders can refer to it.
_Avoid_: Item, SKU (a SKU labels a Variant, not a Product)

**Option**:
A dimension along which a Product's Variants differ, with its allowed values, e.g. Size: 30ml / 50ml / 100ml. Belongs to the Product.
_Avoid_: Axis, attribute

**Variant**:
A specific purchasable configuration of a Product, defined by one chosen value for each of the Product's Options, and carrying its own price and, optionally, a stock level. A Variant with no stock level is **untracked** and always available. Stock goes down when a payment is captured, and is held by a Hold while a payment is in progress. It never goes below zero. Identified by its id; a SKU is an optional merchant-facing code for it.
_Avoid_: SKU (the SKU is a label, the Variant is the thing)

**Bundle**:
A curated, directed pairing from one Product to another that the Merchant recommends buying together, e.g. cleanser → moisturiser. Always Product-to-Product; the Variant is chosen afterwards.
_Avoid_: Combo, routine, kit

**Bundle proposal**:
A Bundle a model has drafted offline from the catalog text, waiting for the Merchant to approve, edit or reject it. Proposals sit in a file in the catalog's own format, and only approved ones are merged into the catalog and seeded. Code validates every proposal (both Products active, different categories, no duplicate, weight 0.5–0.9) before the Merchant sees it.
_Avoid_: AI bundle, suggested bundle, auto-bundle

**Suggestion**:
A Product offered to the Customer as an upsell or cross-sell, whatever produced it: a Bundle, or similarity when no Bundle exists. Chosen by deterministic code, never a model. It always carries its reason (what produced it, and from which Product), so the agent explains it rather than inventing a reason.
_Avoid_: Recommendation, upsell (as a noun)

**Holdout**:
The share of Carts deliberately shown no Suggestions, so a Merchant can measure what Suggestions actually add. It's chosen per Cart, so a whole shopping journey is consistently in or out.
_Avoid_: Control group (that is the eval suite's simulated comparison), A/B test

**Owner**:
Whoever a Cart (and later a Checkout, Order or Refund request) belongs to: the Platform that created it, plus the Customer when the server knows one. Only its Owner may read or change it. Anyone else is told it doesn't exist, in exactly the words used for an id that never existed.
_Avoid_: User, tenant, account (a Merchant is never an Owner here: each deployment serves one)

**Customer**:
The person on whose behalf the agent acts, and to whom Orders and memory are scoped. The server knows who a Customer is only through a Customer token, or, on the Merchant door, the Merchant's own id for them. An email or phone number typed at checkout is never proof. Without either, the caller shops as a guest.
_Avoid_: User, client, buyer, account

**Refund request**:
A Customer's ask, made through an agent, to be refunded for some of an Order's line items. The server either refuses it with a reason or records it as pending approval. A human always decides, and executes any refund in the Razorpay dashboard. TillHand never moves money.
_Avoid_: Refund (that is the money moving, which we never do), return

**Merchant**:
A D2C brand whose catalog TillHand exposes. Each Merchant gets its own deployment; no deployment serves more than one.
_Avoid_: Seller, store, vendor, tenant

### Product

**TillHand site**:
The website where a Merchant signs up and gets its own MCP server. It also hosts TillHand's extension schemas.
_Avoid_: Portal, dashboard, console, landing page

**Storefront**:
A Merchant's own website for human shoppers. It publishes that Merchant's `/.well-known/ucp`, so agents can find its MCP server. Belongs to the Merchant, never to TillHand.
_Avoid_: Shop, site (alone), store

**Demo Merchant**:
The synthetic skincare brand used to show TillHand working end to end. It isn't a real business, and nothing may be built specifically for it.
_Avoid_: Test brand, sample store, our brand

**Platform**:
A third-party agent or app, such as Google's or ChatGPT's, that shops with a Merchant through the public UCP door. It identifies itself on every request with a profile URL.
_Avoid_: Client, consumer, bot

**Profile**:
The JSON document a Platform publishes at a URL to say who it is and what it supports, as UCP defines it. The Platform names that URL on every request, and the server validates the profile before any tool runs, or refuses the call with the reason it can't be used. A **pre-approved** Platform's profile is registered in advance, in the `platforms` table (seeded from `data/platforms.json`), and never fetched. A **key-bound** profile is a Merchant assistant's: it works only on the Merchant door, with one of its Merchant API keys, and is refused on the public door.
_Avoid_: Manifest, agent card

**Merchant assistant**:
The Merchant's own chatbot, on its site or on WhatsApp, which uses the Merchant door with a Merchant API key instead of a profile URL. Claim A is about this.
_Avoid_: Chatbot (alone), widget, our agent

**Public door**:
The MCP endpoint `/mcp`, where any Platform calls the tools, naming itself with its profile URL. That URL is a claim, not proof, so every caller here is a guest.
_Avoid_: Public API, open endpoint

**Merchant door**:
The MCP endpoint `/merchant/mcp`, where the Merchant assistant calls the same tools, proving itself with a Merchant API key and optionally vouching for a Customer with `TillHand-Customer`. A missing, wrong or revoked key gets the same 401. A Merchant assistant's profile works only here.
_Avoid_: Private API, admin endpoint, back door

**Merchant API key**:
The secret a Merchant assistant sends to use the Merchant door, in its own header, never in `Authorization`. Each key maps to exactly one pre-registered profile. Only its hash is stored, and it can be revoked instantly.
_Avoid_: Token, secret (alone), password

**Identity linking**:
UCP's OAuth flow in which a Customer signs in on the Merchant's deployment, which runs its own authorization server, so that an agent can act for them. It only ever adds to what a guest can do, never gates it.
_Avoid_: Login, account linking, SSO

**Customer token**:
What identity linking hands an agent: an opaque bearer token naming one Customer, one Platform registration and its scopes. Only its hash is stored, and revoking it takes effect immediately.
_Avoid_: Session token, access key, JWT

**Platform registration**:
The record that lets one Platform, or a first-party Merchant assistant, ask for Customer tokens: an id, exactly one profile, and exact redirect URLs, added by an admin script. OAuth calls this a client.
_Avoid_: Client (alone), app, integration

**Extension**:
A capability TillHand adds where UCP has none, named under the TillHand site's reversed domain (`app.vercel.tillhand.*`), e.g. refund requests and Suggestions.
_Avoid_: Plugin, custom tool, add-on

### Agent

**Harness**:
The code around the model in our own agent client. It shows the model the tools, runs the calls the model asks for, enforces a step limit and deadlines, retries transport failures, performs the Human bridge, and records every Turn. It decides nothing safety-critical: every rule is enforced by the server, because a Platform's agent never runs our Harness.
_Avoid_: Orchestrator, agent loop, controller

**Turn**:
One Customer message and everything the Harness does until it replies. It ends with a stop reason: answered, step limit, upstream failure, or awaiting payment.
_Avoid_: Step, round, exchange

**Human bridge**:
Showing the Customer something only a human can act on, such as a payment link or a refund-request notice, exactly as the server sent it, then carrying the outcome back into the Session.
_Avoid_: Handoff, escalation (UCP's `requires_escalation` is the server's status, not this)

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
