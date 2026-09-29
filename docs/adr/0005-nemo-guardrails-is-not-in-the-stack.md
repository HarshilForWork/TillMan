# NeMo Guardrails is not in the stack

`Idea.md` named NeMo Guardrails as the guardrails layer, calling it "the closest thing to an industry-standard open-source guardrails stack". Research against the v0.24.0 source (#6) found that it can't do the job that matters here. When one of its rails blocks a tool call, NeMo rewrites the reply text to a refusal, but the call is still returned. NVIDIA's own test asserts this: `assert result["tool_calls"] is not None, "tool_calls preserved despite being blocked"` (`tests/test_tool_calls_event_extraction.py`). v0.24.1, released 16 Sep 2026, is bug fixes only.

Later decisions also left NeMo nowhere to live:
- **The Harness screens nothing** (#10 decision 8).
- **The server, where every rule is enforced, has no LLM in the request path.** NeMo is built to wrap a model call, so on the server it could only be a heavy shell around the Prompt Guard classifier.

We drop it (#13).

## Considered Options

- **Keep it, demoted, as the host for Prompt Guard and output rails:** rejected. It would bring:
  - `fastembed` and `onnxruntime` as unconditional dependencies;
  - `nest-asyncio` patching the event loop under uvicorn;
  - a process-global `NEMOGUARDRAILS_LLM_FRAMEWORK` switch;
  - no streaming.

  All of that for a job a direct classifier call does as well.
- **Keep it for the narrative only:** rejected. A judge who asks "what does NeMo protect?" would get the answer "nothing".

## Consequences

- **Every job NeMo offered has an owner or doesn't apply:**
  - **Prompt Guard:** called directly. Whether it's hosted on Groq or runs in-process (ONNX) is #25's to decide.
  - **NeMo's own jailbreak heuristics:** not needed. They catch 31% of jailbreaks, are English-only, and fail open.
  - **Output rails:** not needed. The server sends only Pydantic-shaped data, never model text (#11 decision 10).
  - **Presidio masking:** #33.
  - **Tracing:** #24.
- **Groq's role as NeMo's "rails LLM" is gone.** Groq stays as the DeepEval judge, and as a candidate Prompt Guard host (#25). That removes a model call from the live request path, which CLAUDE.md forbids anyway.
- **The submission write-up says:** "We evaluated NeMo Guardrails. NVIDIA's own tests show a blocked tool call is still returned to the caller, so it can't protect a money-moving action. TillHand's authority is deterministic server code, which also protects agents that never run our client."
- **Reconsider only if the Merchant assistant needs conversational rails** that its system prompt can't hold, such as staying on-topic or blocking abusive exchanges on WhatsApp. Even then, NeMo would sit in the Harness only, never as a gate on anything.
