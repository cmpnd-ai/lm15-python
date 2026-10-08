# Temporary DSPy integration patches

Upstream release: lm15 v1.2.1 at
`80c7b3a4dd0c024ec3f7c9b37e309325a2ff831b`.
Contract pin: `0f3ea829eeff53e3b944656f2e52c1dc4b358d95`.

Replay these commits in order on a newer eligible release, omitting a patch
only when its regression is covered by the upstream release. Keep fork main
unpatched and do not rewrite published integration branches.

1. `e9b2d42` (original `bf96a4fe32edfa72125a2f9c2ee7ea0cc206c655`):
   enforce the selected model for keyword requests as well as explicit Request
   values. Otherwise a bound client can silently request a different model.
   The regression in tests/test_login.py verifies matching spellings and
   rejection before dispatch for request, plan, complete and stream.
2. `aa13cdfe1767e409e7ca6ded9ba8ebc910ab7f79`
   (original `3a5cac83983aa462841f32a7f9842a085ac052ef`): classify renewal
   HTTP 429 as RateLimitError rather than LoginDenied. A temporary rate limit
   must not delete saved credentials. The regression in tests/test_login.py
   checks preserved credentials/revision, cleared in-flight state, no automatic
   retry and a successful later renewal.

Both fixes originate on cmpnd-ai/lm15-python branch fix/managed-auth-review.
Upstream PR submission was denied by GitHub integration permissions; neither
patch is claimed to be merged upstream. Check for an existing upstream PR
before attempting submission again.

The stale managed account/endpoint routing issue is NOT fixed by this stack.
Retained sync/async adapters can combine a new token with an old account or
endpoint, and logout before dispatch is not checked. DSPy PR #10540 must remain
draft until coherent operation snapshots and dispatch admission are implemented
and tested. Do not mistake the passing existing contract suite for coverage of
these independently reproduced races.

Validation against this package tree: 272 focused tests passed, 2 skipped;
3651 full-suite tests passed, 9 skipped, with only the two known orb reserved-IP
connect-timeout failures. Strict conformance and all 18 pinned contract harness
directions passed. The exact upstream release's CI passed. No live provider
calls were used.
