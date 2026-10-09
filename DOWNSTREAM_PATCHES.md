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
3. `dd0662c866dfd208ffed443f071e10123170ef0e`: keep credentials, account
   headers and endpoints in one per-operation snapshot. Retained adapters
   acquire the active connection for each operation; bound clients retain their
   selected identity. Dispatch checks the snapshot's generation before every
   transport handoff, including delayed streams and multi-wire operations.
   Renewal cannot silently change a known account ID. Regression coverage in
   tests/test_managed_dispatch.py includes sync/async replacement, renewal,
   logout races, batch upload/submit, explicit endpoints and offline planning.

The first two fixes originate on cmpnd-ai/lm15-python branch fix/managed-auth-review.
Upstream PR submission was denied by GitHub integration permissions; neither
patch is claimed to be merged upstream. Check for an existing upstream PR
before attempting submission again.

The third fix is a downstream implementation pending upstream contribution.
Managed websocket operations and raw request builders that bypass operation
preparation fail closed; explicit/unmanaged credentials retain their existing
behavior. No shared adapter fields are mutated during requests. A snapshot
admitted before logout may still reach the transport: logout cannot recall an
already admitted request. Keep DSPy PR #10540 draft until its integration and
review follow-through complete; passing existing contract tests alone does not
demonstrate the new race regressions.

The first two patches were validated with 3651 tests passing, 9 skipped and the
two approved orb reserved-IP connect-timeout failures. For the third patch,
72 focused auth/dispatch tests passed; the retained-adapter endpoint regression
fails against the two-patch baseline. Full source, strict conformance, pinned
contract and type checks must be rerun on every changed stack. No live provider
calls are needed.
