# The Harness consumes its own MCP server over HTTP

TillHand's Claim B is that any capable third-party agent could discover this merchant and hand off into our tools. The obvious implementation is for our own Harness to import the tool functions directly, since it lives in the same codebase — but then the MCP transport is never exercised by anything we run, and the claim is untested. So the Harness connects to the MCP server as a real MCP client over streamable HTTP, walking the exact path a third-party agent would walk.

## Consequences

- Tools are defined once as plain Python functions; the MCP server and any direct caller both wrap that single definition, so there is no second source of truth.
- The eval suite is allowed to bypass the transport and call the functions directly, because there the transport is not what is under test and the latency is not worth paying per scenario.
- We pay one network hop per tool call in the demo path. That is the cost of the claim being honest.
