# Temporary DSPy integration patches

Upstream release: lm15 v1.3.0 at
`505eaf5044556b791a62f684a2e75e56861a517a`.
Contract pin: `88bb560118243bd6601cf33e6addcb586b568469`.

Replay these commits in order on a newer eligible release, omitting a patch
only when its regression is covered by the upstream release. Keep fork main
unpatched and do not rewrite published integration branches.

1. `48986e0` (original `bf96a4fe32edfa72125a2f9c2ee7ea0cc206c655`):
   enforce the selected model for keyword requests as well as explicit Request
   values. Otherwise a bound client can silently request a different model.
   The regression in tests/test_login.py verifies matching spellings and
   rejection before dispatch for request, plan, complete and stream.
2. `2f49d32` (original `3a5cac83983aa462841f32a7f9842a085ac052ef`):
   classify renewal HTTP 429 as RateLimitError rather than LoginDenied. A
   temporary rate limit must not delete saved credentials. The regression in
   tests/test_login.py checks preserved credentials/revision, cleared in-flight
   state, no automatic retry and a successful later renewal.
3. `c29b9c1` (original `dd0662c866dfd208ffed443f071e10123170ef0e`):
   keep credentials, account headers and endpoints in one per-operation
   snapshot. Retained adapters acquire the active connection for each
   operation; bound clients retain their selected identity. Dispatch checks
   the snapshot's generation before every transport handoff, including delayed
   streams and multi-wire operations. Renewal cannot silently change a known
   account ID. Regression coverage in tests/test_managed_dispatch.py includes
   sync/async replacement, renewal, logout races, batch upload/submit, explicit
   endpoints and offline planning.
4. `7a7ade1` (original `046f2bfa5c12b55c6a199f388879e5d689397a87`):
   preserve the retained adapter's transport and adaptation policy when
   preparing an operation. Sync/async regressions verify the documented
   transport replacement escape hatch sends only through the caller's
   replacement transport.

All four patches remain necessary in v1.3.0. Its Meta login changes supply
the minted endpoint and plan; they do not change bound selection, renewal
HTTP 429 handling or managed dispatch. The replay has no patch-content
changes from the validated v1.2.3 stack. Meta login remains unverified and
requires explicit opt-in because of the documented terms and data-use risks.

The first two fixes originate on cmpnd-ai/lm15-python branch
fix/managed-auth-review. The routing fixes are published on
fix/managed-request-snapshots. Upstream PR submission was denied by GitHub
integration permissions; no patch is claimed to be merged upstream. Check
for an existing upstream PR before attempting submission again.

Managed websocket operations and raw request builders that bypass operation
preparation fail closed; explicit/unmanaged credentials retain their existing
behavior. No shared adapter fields are mutated during requests. A snapshot
admitted before logout may still reach the transport: logout cannot recall an
already admitted request.

Full source tests, strict conformance, the exact pinned contract harness,
type checks and DSPy integration tests must pass on every changed stack before
import. The only approved orb exceptions are the sync/async reserved-IP
connect-timeout tests, with passing exact-release upstream CI. Do not infer
that passing existing contract tests alone demonstrates the race regressions.
No live provider calls are needed.
