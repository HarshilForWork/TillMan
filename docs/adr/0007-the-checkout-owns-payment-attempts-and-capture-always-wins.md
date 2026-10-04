# The Checkout owns payment attempts, and capture always wins

UPI payments complete asynchronously. The Customer approves in their own bank app minutes later, Razorpay reports the outcome by at-least-once, out-of-order webhooks, and a payment marked failed can still be authorised up to three days later ("late authorisation"). The full design, with diagrams, failure walkthroughs and the reasoning behind each choice, is in `docs/architecture/payments.md`. This ADR records the decisions a later reader would otherwise question (#30).

## Decisions

- **Copy UCP's objects.** Our **Checkout** (UCP Checkout) owns every **PaymentIntent**, one Razorpay order per attempt, with `receipt` = `{checkout}-{attempt}`. Our **Order** (UCP Order) is created only when a payment is captured, so an Order always means "paid". This replaces the earlier glossary, in which an Order existed before payment. The invariant that ours and Razorpay's objects stay separate is unchanged.
- **Forward only, enforced in the database.** Three mechanisms:
  - conditional updates (`… WHERE status = <allowed from>`);
  - a trigger rejecting backward moves;
  - an append-only webhook event log, unique on Razorpay's event id.
- **`captured` beats everything.** A `failed`, `cancelled` or `expired` PaymentIntent may still become `captured`, and nothing leaves `captured`. Capture is automatic, so a payment never sits authorised long enough for Razorpay to auto-refund it.
- **Capture always creates an Order,** even for a Checkout that has expired or been cancelled. It's flagged `late_payment`, or `oversold` when stock ran out, for the Merchant to fulfil or refund by hand. We never call the refund API.
- **Never two payable links.** A retry cancels the previous attempt's link before creating a new one. If that link was already paid, the purchase is simply complete.
- **A Hold while a payment is in progress.** This changes #8's "no reservation". `complete_checkout` takes a `SELECT … FOR UPDATE` lock on the Variant row for milliseconds, checks stock minus active Holds, and inserts a Hold that expires with the link. Capture converts the Hold into the decrement, in the same transaction as the event insert and the Order. Stock never goes below zero.
- **Three paths into one transition function:** webhooks, reconcile-on-read (`get_checkout` asks Razorpay when a Checkout has gone unchecked for 30 s), and a scheduled reconcile script for late authorisations.
- **Idempotency keys** live in Neon for 48 h, keyed by key, caller and operation. They're written in the same transaction as the state change they protect, and the call fails closed if the store fails.

## Considered Options

- **Our Order existing before payment** (the old glossary): rejected. Every tool would have to translate between our meaning and UCP's.
- **No Hold** (#8's original): rejected. Two Customers could both pay for the last unit, and one would need a manual refund.
- **A Hold at Cart or Checkout creation, like picking a seat:** rejected. Agents and bots could hoard stock by opening Checkouts they never pay for.
- **A long database lock held while the Customer pays:** rejected. It would freeze every other buyer, and it breaks the rule against holding a connection across slow work.
- **Ignoring, or auto-refunding, late money:** rejected. Ignoring strands a Customer who paid, and auto-refunding breaks "we never call the refund API".

## Consequences

- **Holds can't eliminate overselling,** because late authorisation can capture money days after a Hold expired. The `oversold` flag is the deliberate backstop.
- **The UCP status while waiting for the Customer's UPI approval** (`complete_in_progress` or `requires_escalation` with a `continue_url`) is #16's to decide. The lifecycle rules here hold either way.
