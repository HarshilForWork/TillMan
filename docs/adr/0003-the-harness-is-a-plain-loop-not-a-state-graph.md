# The Harness is a plain loop, not a state graph

The original plan had the Harness as an explicit state machine, built in LangGraph, whose job was deciding whether a tool call was permitted. Authority moved to the server. That removed the first reason for a graph. The ticket's remaining test was whether the flow has real branching recovery: re-planning when an item is out of stock, a declined payment, resuming after a webhook. It doesn't. The first two come back as normal server results that the model re-plans from, and the third is a `get_order` call, because the server holds every Cart and Order. The Human bridge is spec-defined: UCP's `requires_escalation` status always carries a `continue_url`. So it is one deterministic check in code, not a graph edge. The Harness is therefore a hand-written async loop over `google-genai` and our own `mcp` 2.x client: ask the model, run the calls it asks for in order, feed the results back, and stop on a text answer or a limit.

This was settled by a debate with a neutral judge (#10). The for-LangGraph side ended up agreeing, on this evidence, checked on 28 Sep 2026:
- **`interrupt()` needs a checkpointer and a `thread_id`.** That is a third store of conversation state, next to Neon and the caller-held transcript, and it needs psycopg as a second Postgres driver. Ending the Turn already expresses a pause.
- **`langchain-mcp-adapters` 0.3.1 has no `mcp` upper bound.** It fails on import against `mcp` 2.2 (reproduced), and 0.3.2 pins `mcp<2`. Its successor, `langchain[mcp]`, is beta and goes through `fastmcp`.
- **`langchain-google-genai` 4.4.0 drops the call `id` from `FunctionResponse`,** and bypasses Gemini 3's `thoughtSignature` check when a signature is missing. The plain loop appends the model's `Content` verbatim, and signatures and ids survive a JSON round-trip.
- **`recursion_limit` counts supersteps, not tool calls,** and it raises instead of ending in a Turn record.

## Considered Options

- **LangGraph with the Functional API and no checkpointer:** rejected. With the adapters and `interrupt()` set aside, what's left is a decorator and about 19 more packages. Its real advantage, streaming steps with `astream`, isn't needed while a Turn returns one record.

## Consequences

- **Every Harness duty is visible in the loop:** the step limit, retries, the Human bridge check, one tracing span per call, and the Turn record behind DeepEval's `tools_called`. The owner is learning harness engineering, and this is the part a framework hides.
- **LangGraph is reconsidered, without its MCP adapter, only when one of these becomes true:**
  1. A shipped flow must pause inside a single Turn and resume in the same process, and ending the Turn can't express it.
  2. A UI needs step-by-step streaming, and the hand-written async generator has grown beyond a small helper.
  3. The Harness needs parallel branches of agent work, such as sub-agents or fan-out and merge. Several tool calls in one model reply doesn't count.
  4. The loop's control flow grows past about 150 lines.
- **A refund approval is not a trigger.** A human executes it in the Razorpay dashboard, outside the chat. Moving approval into the chat would be a design change with its own ADR.
