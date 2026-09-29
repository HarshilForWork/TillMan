# Each deployment is its own OAuth authorization server, with opaque tokens

UCP identity linking lets a Platform act for a signed-in Customer. It's how an agent such as Google's sees a Customer's past Orders, and how a Merchant assistant on a site with no login of its own knows who it's talking to. At tag `v2026-08-25` a business that offers it **MUST** run an OAuth 2.0 authorization server (`identity-linking/index.md:200-204`). So each Merchant's deployment runs one, and we build it on the route. The design is settled now and built after the core purchase flow works (#11). Linking only ever adds to what a guest can do (`:31-32`), so guest browsing and checkout never depend on it.

## Decisions

- **Customers sign in with Google or an email code** on our authorize page. We remain the authorization server and issue our own tokens. Phone OTP is ruled out: DLT registration needs a real registered business. Passwords are ruled out: we would be storing them. We never auto-link accounts because an email matches.
- **Built on `mcp.server.auth`,** which is already installed and async, with three fixes for UCP:
  - our own RFC 8414 metadata;
  - the RFC 9207 `iss` parameter;
  - loopback redirect matching that ignores the port.

  Authlib was rejected because its server core is sync, and it would block the event loop on every Neon lookup.
- **Platform registrations are added by hand** with an admin script: pre-registered, never self-registered (DCR). Each one is bound to exactly one UCP profile and exact redirect URLs (OV:2198, OV:2259). Client authentication is `none`, with PKCE S256 required. A `first_party` registration serves a Merchant assistant, and it skips the consent screen.
- **Two scopes only: `order:read` and `order:manage`.** Listing a scope gates its operations, so cart and checkout scopes are deliberately not listed, and guest checkout keeps working.
- **Opaque tokens, stored hashed in Neon.** Access tokens last 1 hour. Refresh tokens last 30 days and rotate on each use. Revoking a refresh token kills its access tokens at once.

## Considered Options

- **JWT access tokens (RFC 9068):** rejected. Their one advantage is that a separate service can check them without the database. Here the authorization server and the tools are one process. The spec also requires revocation to take effect immediately (`:247-250`), which forces a database check on every request anyway. JWTs would add a signing key per deployment, which forges tokens if leaked, and a payload anyone can read in a log, while saving nothing. The token lookup rides in the same Neon query the operation already makes.
- **A hosted identity provider (e.g. Auth0) as the authorization server:** rejected for now. It's a new external dependency and a cost, when the SDK already provides the async parts.
- **Deferring identity linking to a later ticket:** rejected by the owner. Designing it now keeps cart, checkout and memory from being built in a way that blocks it.

## Consequences

- **The same "hash it, store it, look it up" pattern** covers Merchant API keys, email codes and Customer tokens.
- **An unregistered Platform can't link accounts,** but it can still browse and check out as a guest.
- **Reconsider JWTs only if** token checking moves to a service separate from the authorization server.
- **Add `private_key_jwt`** client authentication (a SHOULD) when a Platform asks for it.
