# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A19).
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
