# EMB-54 spike — xAI native SDK vs the Anthropic-compat layer

**Verdict: NO-GO on a native gRPC transport.** The caching economics that
motivated the spike turn out not to be a compat-shim artifact for the path
we actually run, and a native transport would buy a new credential problem
for no measured benefit.

## Question 2 — are the pain points shim artifacts? (answered empirically)

**Caching.** xAI's docs describe prompt caching as per-server, with cache
hits dependent on the `x-grok-conv-id` sticky-routing header — and list
native REST, gRPC, and the OpenAI-compat layer as the supported surfaces
(the Anthropic-compat surface is absent). That made "we never send
conv-id" the leading suspect for the 4× cost gap ($8.28 Agent-SDK vs
$2.17 direct on identical QA runs).

Measured 2026-07-23, identical standalone grok-4.5 QA runs on ai-quoting
through the DirectXaiExecutor:

| Run | Cost | Input tokens | cache_read | Ratio |
|---|---|---|---|---|
| Baseline, no header (`job-00f95860443d`, 07-22) | $2.17 | 1,055,036 | 998,400 | 94.6% |
| With proxy-stamped `x-grok-conv-id` (`job-ebb1f568358e`) | $1.99 | 972,540 | 917,120 | 94.3% |

The compat surface reports (and prices) ~95% cache reads for the direct
executor's prefix-stable request stream **with or without the header** —
the $0.18 delta tracks run length (7.3k vs 10.2k output tokens), not
caching. Conclusion: caching is NOT broken on the Anthropic-compat surface
for this request pattern, so it is not what a native transport would fix.

The proxy now stamps `x-grok-conv-id` with the enrolled sandbox identity
anyway (#49): zero-cost insurance against xAI's server pool growing, per
their documented mechanism, benefiting both grok paths.

**The 4× gap therefore lives in the Agent-SDK path itself.** Its runs
report zero usage into `traces` (the $8.28 figure is the Claude Code CLI's
own accounting), and its request pattern differs from the direct
executor's. Since EMB-52 made the direct executor the default, the SDK
path is a fallback — an optional ~$8 experiment (one SDK run post-#49)
would show whether conv-id changes its economics, but nothing routes there
by default.

**Schema strictness** is already fully handled at the client (#35) and the
proxy (#37); a native SDK would not remove any embry0-side code.

## Question 1 — SuperGrok bearer on native gRPC (not empirically tested)

The native SDK is designed around `XAI_API_KEY`; auth rides gRPC metadata
as `authorization: Bearer <credential>` — the same shape the OAuth access
token uses on HTTP, so it *may* authenticate, but extracting a live token
for the probe was deliberately not done (production credential, rotating
refresh lineage). With the no-go verdict the question is moot; revisit
only if a native transport is ever reconsidered.

## Question 3 — cost of a native transport (estimated, not built)

~2–4 days: a new executor loop on `xai-sdk` chat + tool-call mapping, plus
a credential path the HTTP xai-proxy cannot provide (gRPC does not ride a
path-rewriting HTTP proxy — the token would need sandbox delivery or an
orchestrator-side executor). Only the DirectXaiExecutor could adopt it;
the Agent-SDK path is Anthropic-wire by construction.

## EMB-52 economics

Unchanged: the direct executor stays the right default (cheap, ~95%
cached, now conv-id-stamped). No revisit needed.
