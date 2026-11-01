# Temporary DSPy integration patches

Upstream release: lm15 v2.0.0 at
`b6c9159b965d0aac8fca919b87f7e943a61ce953`.
Contract pin: `cfc3edc0d31127ecab55240f866dd3c54df7f07b`.

Replay these commits in order on a newer eligible release, omitting a patch
only when its regression is covered by the upstream release. Keep fork main
unpatched and do not rewrite published integration branches.

The v2.0.0 replay retains all ten patches, in the same order:

1. `df7d2448746864d4a232b842b52f00634f9a61d3`
2. `2038002904f4dbe8870c5743fa86a0089057e22d`
3. `ce24bb704246fd3feafa7b84c90413f21114d234`
4. `5c0f33dd7d81c7ec824591028f755f51fa9ca2ce`
5. `1816a4306398251f32da5ff5fd39c26d435d1abb`
6. `0ee1c1b75eb386cefd14e0acc7dd323f588303c8`
7. `59f0d97fd4a291d3d3c8bd3a9e65ad60c4c82b3d`
8. `26f2e96399678932feec69a0301ecfcc35ff9a6b`
9. `a1771010164e3b4f6e5be7c01403afb0dcd1056f`
10. `81ff7871beedd3a2368c060792ed091f9ee3c89d`

Upstream's release changes usage normalization and pricing, not the managed
identity/login defects below. All patches replay without conflicts; provider
changes compose with the existing operation snapshots. Source validation:
3,797 passed, 9 skipped, and only the two approved reserved-IP connect-timeout
exceptions; exact-release upstream CI passed. Strict conformance, all pinned
contract harness directions, constructor signatures and type checks pass.
The stack is on integration/lm15-2.0.0-patches and the separate contribution
branch fix/managed-request-snapshots-2.0.0. Prior published stacks remain intact.

The following numbered motivations retain their v1.5.0 and original commit
identities for traceability; each corresponds to the same replay number above.

1. `cc8a5b20763718faf4a70a1f8c5bd902eccb7827` (original `bf96a4fe32edfa72125a2f9c2ee7ea0cc206c655`):
   enforce the selected model for keyword requests as well as explicit Request
   values. Otherwise a bound client can silently request a different model.
   The regression in tests/test_login.py verifies matching spellings and
   rejection before dispatch for request, plan, complete and stream.
2. `b0f32a1cb566c9cf0559051e66333fa3115e5f7e` (original `3a5cac83983aa462841f32a7f9842a085ac052ef`):
   classify renewal HTTP 429 as RateLimitError rather than LoginDenied. A
   temporary rate limit must not delete saved credentials. The regression in
   tests/test_login.py checks preserved credentials/revision, cleared in-flight
   state, no automatic retry and a successful later renewal.
3. `2f0d9b7fe7158778b4a3edc1a3087b13142bb29d` (original `dd0662c866dfd208ffed443f071e10123170ef0e`):
   keep credentials, account headers and endpoints in one per-operation
   snapshot. Retained adapters acquire the active connection for each
   operation; bound clients retain their selected identity. Dispatch checks
   the snapshot's generation before every transport handoff, including delayed
   streams and multi-wire operations. Renewal cannot silently change a known
   account ID. Regression coverage in tests/test_managed_dispatch.py includes
   sync/async replacement, renewal, logout races, batch upload/submit, explicit
   endpoints and offline planning.
4. `dfb1e390073fbb6a2ff668996f9d2bb7e394dd0a` (original `046f2bfa5c12b55c6a199f388879e5d689397a87`):
   preserve the retained adapter's transport and adaptation policy when
   preparing an operation. Sync/async regressions verify the documented
   transport replacement escape hatch sends only through the caller's
   replacement transport.
5. `37ebcfbc66899e18633663034bd27788ed8f8c5f` (original `8671868feb55408c2a11bbea2413f9a7d70ebe9b`): expose the descriptor
   collection as `lm15.login_providers` (also `lm15.login.providers`).
   The attempted lazy `providers` export was hidden by the existing provider
   implementation package. Keep that package and its submodules intact.
   Fresh-interpreter import-order regressions live in tests/test_login_exports.py.
6. `89df856db40f0a4f2b9926e20b7184f21de6033b` (original `cd7ef071d0399af28b0e88a71bfd11f97a859cb8`): wait for dismissed terminal
   manual input to relinquish stdin before returning to a subsequent picker.
   The existing "press Enter to continue" interaction remains; no portable
   cancellation of blocking input is assumed. Tests/test_terminal_login.py
   covers a real loopback callback with controlled input, early dismissal,
   completed reads and interrupted reads.
7. `ff3f297ed5795f02d203c32bf1e26c204bfa4d71` (original `a838f91648c844416d75697273c38646caddec8f`): resolve sibling routes to
   their advertised saved connection for requests, renewal, status and
   logout; explicit route slots (including logout markers) take precedence.
   Async task cancellation signals and durably releases only its own login
   attempt before returning CancelledError. A commit already won is not
   undone. Verification pins the checked connection and records results
   only if its identity/generation still matches. Tests/test_login.py covers
   all three Meta routes with remint/endpoint change/logout, cooperative and
   non-cooperative cancelled login workers, and valid/rejected verification
   with and without concurrent replacement.
8. `07816eca2bda2d7ee149266b48a92c430377293c` (original `c9d45beb2744835a79bc46ea7aa8d2e209f9139e`): keep the post-approval
   cancellation/deadline check inside the existing typed-error boundary.
   Self-review of patch 7 reproduced a raw LoginExpired escaping if approval
   finished after the deadline. The public regression in tests/test_login.py
   now gets AuthOperationError(reason="login_expired"), persists no credential,
   and can start a new login immediately. This patch is downstream only.
9. `d8dab6150caf86e0328385c89475d8255ac3fd88` (original `dd900d292a393c6c04fab7d1c57248dfee25e90b`): preserve the originally
   requested route through renewal and re-resolve it at dispatch admission.
   A newly saved or signed-out route-specific slot must supersede the shared
   owner even for an already prepared request. Run durable async login
   cancellation on an independent executor so a blocked login cannot occupy
   the only worker available to release its reservation. The second Greptile
   review reproduced both races. Four sync/async route replacement/logout
   cases and two one-worker cancellation cases fail before and pass after.
   Cancellation frees the reservation before the login worker is released;
   it does not interrupt blocking terminal input or undo a completed commit.
   These fixes are downstream only, pending upstream contribution access.
10. `d2c73eb1e5099f9ddd53e638ccd8071a907d55f3` (original `359f90953dd90089c306ceb5a69fe10bbb14c483`): preserve the selected account.
    Bound clients and saved-account model listing
    discard config identity overrides while retaining execution settings;
    ordinary routers keep their explicit-identity precedence. The original
    cloud-settings runtime hunk is omitted: upstream PR #21, released in
    v1.4.1, now owns it. Retain the public sync/async replacement regressions
    to verify that upstream settings resolution composes with patch 3.
    The October 15 Greptile review reproduced wrong-account dispatch and
    ignored project/region selection. Ten public-operation cases fail before
    and pass after, covering catalog/inference keys, named cloud identities,
    logout/replacement, absent/conflicting ambient settings and partial
    explicit overrides. Tests live in tests/test_login.py and
    tests/test_managed_dispatch.py. This patch is downstream only, pending
    upstream contribution access.

During the v1.5.0 replay, the first nine patches remained necessary. Conflicts preserved
upstream's measurable-judgment filtering, bounding_box export and typed
Connection declaration alongside our snapshot and descriptor fixes. Patch 10
now changes only bound identity selection plus regressions; it has no router
runtime diff. All retained regression tests pass, including eight cloud
settings/replacement cases using the upstream implementation.
Meta login remains unverified and requires explicit opt-in because of the
documented terms and data-use risks.

Patches 5–7 address five reproduced findings from DSPy #10540's October 13
Greptile review. The focused before/after run failed 14 cases on the previous
tip (with 3 controls passing); all pass after the fixes. These new patches
are downstream only and are published on the integration and contribution
branches below, not claimed submitted or merged upstream.

The first two fixes originate on cmpnd-ai/lm15-python branch
fix/managed-auth-review. The routing fixes are published on
fix/managed-request-snapshots, with the 1.3.0 stack and review fixes on
fix/managed-request-snapshots-1.3.0 and integration/lm15-1.3.0-patches.
The v1.5.0 replay is on fix/managed-request-snapshots-1.5.0 and
integration/lm15-1.5.0-patches; prior branches remain unchanged.
Upstream PR submission was denied by GitHub
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
