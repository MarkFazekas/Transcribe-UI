# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A18).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

**A12. Docker Hub repository and image tags?**
Needed: the Docker Hub namespace/repository name, and a Docker Hub access token stored as the
GitHub secrets `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`.
Default tags: `sha-<short>`, `latest` and `schema-<contracts major>`. The desktop app uses the
`schema-<major>` tag of the contracts it was built with. (Pinning `sha-` would break when a
desktop-only change does not rebuild the image.)
Assumption until answered: `release-worker.yml` builds and
smoke-tests the image but skips the push, so no `schema-<major>` tag exists yet and the desktop
app cannot launch a worker.
