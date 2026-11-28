"""The flags of `credentials_status` that every frontend follows (architecture §3.6, §3.9 "Roles").

`r2_set`: every R2 credential found, or the keyring failed while reading (it may hold them).
`runpod_key_set`: the same rule for the Runpod key.
"""

from support.credentials import LockedKeyring, memory_store
from transcribe_ui.credentials.store import CredentialStore

# The R2 credentials in the environment, all but the secret.
PARTIAL_R2 = {"R2_ENDPOINT_URL": "https://example.invalid", "R2_BUCKET": "jobs", "R2_ACCESS_KEY_ID": "id"}


def test_partial_r2_is_not_set() -> None:
    """Three of the four R2 credentials: not set; no Runpod key either."""
    lookup = memory_store(PARTIAL_R2).lookup()
    assert (lookup.r2_set(), lookup.runpod_key_set()) == (False, False)


def test_a_failed_keyring_may_hold_everything() -> None:
    """A keyring that failed while reading: both count as set, so the steps report the keyring's error."""
    lookup = CredentialStore(LockedKeyring, {}).lookup()
    assert (lookup.keyring, lookup.r2_set(), lookup.runpod_key_set()) == ("failed", True, True)
