# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A20).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

### A18. R2 credentials for the pod

§5.4 says the worker's bucket-scoped R2 token is stored as Runpod secrets referenced from a
template the desktop app maintains, but not who creates the secrets or whether the pod gets the
desktop's own token. Both need Object Read & Write on the same bucket (the desktop also deletes
for cleanup), and R2 cannot scope by prefix, so a second token adds only separate revocation.

Proposed default: one bucket-scoped token. `setup_cloud` (plan `plans/m5.md`, M5.6) creates or
updates the Runpod secrets from the R2 credentials in the desktop's keyring, then the template
that references them; a token rotation is a re-run of `setup_cloud`.

### A19. Maximum wait for a machine

The pod name's deadline is fixed when the pod is requested: queue `expiresAt` +
`MAX_RUNTIME_S` + 10 min (`plans/m5.md` §1, M3 reaper). A queued pod that deploys early and
then fails before Python starts (image pull failure, crash loop: termination layers 1-2 never
run) is caught only by the reaper at that deadline, so the longest allowed wait adds directly
to the worst-case billing (0.27 USD/h). The plan first allowed up to 7 days (Runpod allows 30).

Proposed default: the wait is 5 min to 24 h (default 1 h, as in the requirements). With
`MAX_RUNTIME_S` capped at 24 h, the worst case is under 50 h (about 13 USD). Alternative: allow
longer waits and let the reaper measure from the pod's own start time instead; that needs the
runtime in the pod name and a start-time field in the v2 pod object (not verified), so an M3
change.
