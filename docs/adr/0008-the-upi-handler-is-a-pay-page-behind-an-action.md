# The UPI handler is a pay page behind an Action, with no credential

UCP's live payment handlers assume the agent hands over a token that `complete_checkout` charges synchronously. UPI has no such token: a human approves in their own bank app. TillHand's handler, `app.vercel.tillhand.razorpay_upi`, is the first UPI handler for UCP. The full design, with the worked examples, the facts and the interview Q&A, is in `docs/architecture/payments.md` §11. This ADR records what a later reader would otherwise question (#16).

## Decisions

- **The surface is our own pay page** (`/pay/{payment_intent}` on the Merchant's deployment), running Razorpay's Standard Checkout widget for that attempt's Razorpay order. In test mode it takes the test UPI id `success@razorpay`. Live, the widget shows a QR code or the UPI-app chooser, with no code change.
- **`complete_checkout` returns `complete_in_progress` plus a handler-specific Action** (the 3-D Secure pattern), with `continue_url` as the fallback in every response. The purchase completes from Razorpay's webhook. A small Checkout extension declares the Action type.
- **The instrument is `type: upi` with no credential.** The human's approval in their bank app is the consent.
- **"Never two payable" holds at our edge:** a superseded attempt's page refuses to open, and its widget times out with the attempt's window. A payment that slips through anyway is recorded, creates no second Order, and flags the Order `duplicate_payment` for a manual refund.
- **The approval window is 15 min, shrinking to the Checkout's remaining time.** The call is refused (`checkout_expiring`) under 2 min. A Hold lasts the window plus 2 min.
- **Failures map to UCP codes with severities** (`payment_failed`, `upstream_timeout`, `out_of_stock`, `checkout_expiring`, `invalid_instrument`), as the spec's handler guide requires.
- **There's no mode-specific code.** Test vs live is the keys plus a domain allowlist.
- **Human-absent payment (UPI Reserve Pay) is a future instrument type** under the same handler. AP2 is #20.
- **The spec, schema and Action extension are published from `web/site/`** on `tillhand.vercel.app`.

## Considered Options

- **Razorpay Payment Links:** the documented fallback, not the primary. They have a real cancel API, but in test mode they're capped at 30 per account, and a link creates its own Razorpay order, so our per-attempt `receipt` can't be used.
- **The Razorpay QR Codes API and raw UPI intent:** rejected. They can't be paid in test mode, they need Razorpay Support to enable them, and a QR can't carry our reference.
- **`requires_escalation` + `continue_url` as the primary path:** rejected. The spec frames escalation as a fallback for "information the API can't collect", and its example re-calls Complete. An Action fits "the buyer acts, the provider confirms" and keeps #30's polling model.
- **A second Order for a second capture:** rejected, because the Customer paid twice by accident, not ordered twice. **Ignoring the capture** is rejected too: money that arrives is never lost.

## Consequences

- **This revises ADR-0007's "a retry cancels the previous attempt's link":** Razorpay orders can't be cancelled, so the old attempt is *superseded* at our edge instead. The real cancel applies only on the Payment Link fallback.
- **Invariant I5 now reads:** every capture is recorded and reaches a human-visible outcome. The first creates the Order; a later one flags it `duplicate_payment`.
- **Live mode needs the pay page's domain allowlisted** for the widget's `callback_url`.
