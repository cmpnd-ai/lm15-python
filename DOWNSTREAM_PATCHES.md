# Temporary DSPy integration patches

Upstream release: lm15 v1.4.0 at
`48ab49d84257873e63329c62712973cd0c838363`.
Contract pin: `10645e5f6a69d9d7a365bdb62de9abac1f4dcffc`.

Replay these commits in order on a newer eligible release, omitting a patch
only when its regression is covered by the upstream release. Keep fork main
unpatched and do not rewrite published integration branches.

1. `2d3730fb1de1b501a08cb457e5a3242da77ca141` (original `bf96a4fe32edfa72125a2f9c2ee7ea0cc206c655`):
   enforce the selected model for keyword requests as well as explicit Request
   values. Otherwise a bound client can silently request a different model.
   The regression in tests/test_login.py verifies matching spellings and
   rejection before dispatch for request, plan, complete and stream.
2. `1e04a5a2edd6d81e9d2940f08b7ce87d11caa013` (original `3a5cac83983aa462841f32a7f9842a085ac052ef`):
   classify renewal HTTP 429 as RateLimitError rather than LoginDenied. A
   temporary rate limit must not delete saved credentials. The regression in
   tests/test_login.py checks preserved credentials/revision, cleared in-flight
   state, no automatic retry and a successful later renewal.
3. `f6a7f8bb8bc13c247053a5a42728bbd9c008fe13` (original `dd0662c866dfd208ffed443f071e10123170ef0e`):
   keep credentials, account headers and endpoints in one per-operation
   snapshot. Retained adapters acquire the active connection for each
   operation; bound clients retain their selected identity. Dispatch checks
   the snapshot's generation before every transport handoff, including delayed
   streams and multi-wire operations. Renewal cannot silently change a known
   account ID. Regression coverage in tests/test_managed_dispatch.py includes
   sync/async replacement, renewal, logout races, batch upload/submit, explicit
   endpoints and offline planning.
4. `7d344a10ee0db3cec5516dbdffe496f1d36760a8` (original `046f2bfa5c12b55c6a199f388879e5d689397a87`):
   preserve the retained adapter's transport and adaptation policy when
   preparing an operation. Sync/async regressions verify the documented
   transport replacement escape hatch sends only through the caller's
   replacement transport.
5. `5211b7df922247c82a6639a1c1fe013e9a0491c6` (original `8671868feb55408c2a11bbea2413f9a7d70ebe9b`): expose the descriptor
   collection as `lm15.login_providers` (also `lm15.login.providers`).
   The attempted lazy `providers` export was hidden by the existing provider
   implementation package. Keep that package and its submodules intact.
   Fresh-interpreter import-order regressions live in tests/test_login_exports.py.
6. `3e9269f199a450b105637b4db6764a04920b454e` (original `cd7ef071d0399af28b0e88a71bfd11f97a859cb8`): wait for dismissed terminal
   manual input to relinquish stdin before returning to a subsequent picker.
   The existing "press Enter to continue" interaction remains; no portable
   cancellation of blocking input is assumed. Tests/test_terminal_login.py
   covers a real loopback callback with controlled input, early dismissal,
   completed reads and interrupted reads.
7. `a91003b66b64fea90d1fd104429dee1585889007` (original `a838f91648c844416d75697273c38646caddec8f`): resolve sibling routes to
   their advertised saved connection for requests, renewal, status and
   logout; explicit route slots (including logout markers) take precedence.
   Async task cancellation signals and durably releases only its own login
   attempt before returning CancelledError. A commit already won is not
   undone. Verification pins the checked connection and records results
   only if its identity/generation still matches. Tests/test_login.py covers
   all three Meta routes with remint/endpoint change/logout, cooperative and
   non-cooperative cancelled login workers, and valid/rejected verification
   with and without concurrent replacement.
8. `5c4770348454193b9a6abfba69d1a3419faf5652` (original `c9d45beb2744835a79bc46ea7aa8d2e209f9139e`): keep the post-approval
   cancellation/deadline check inside the existing typed-error boundary.
   Self-review of patch 7 reproduced a raw LoginExpired escaping if approval
   finished after the deadline. The public regression in tests/test_login.py
   now gets AuthOperationError(reason="login_expired"), persists no credential,
   and can start a new login immediately. This patch is downstream only.
9. `a3a526edffb18ca2f8818e9dc14da31c356e4da6` (original `dd900d292a393c6c04fab7d1c57248dfee25e90b`): preserve the originally
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

All nine patches remain necessary in v1.4.0: the only runtime changes from
v1.3.0 are its version and TypeSafe image encoding. None of the patched
login/routing owners changed. The replay has no patch-content changes from
the validated v1.3.0 stack; each replay commit records its previous commit.
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
The v1.4.0 replay is on fix/managed-request-snapshots-1.4.0 and
integration/lm15-1.4.0-patches; prior branches remain unchanged.
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
