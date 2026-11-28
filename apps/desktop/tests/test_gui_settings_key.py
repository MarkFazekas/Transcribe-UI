"""The settings area follows the backend's rule for the Runpod key (plan m6 M6.13; architecture §3.9 "Roles").

A keyring that failed while reading may hold the key: `credentials_status` answers
`runpod_key_set` true, so the cloud setup is offered (its check reports the keyring's error)
instead of saying that the Runpod API key is needed.
"""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from support.fakes import seeded_ports
from support.gui_settings import Deferred, Scripted
from transcribe_ui.api.backend import Backend
from transcribe_ui.credentials.classes import CredentialsStatusResponse, CredentialStatus
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.settings.accounts import AccountsViewModel
from transcribe_ui.gui.settings.cloud_setup import CloudSetupViewModel
from transcribe_ui.gui.settings.hooks import AreaHooks
from transcribe_ui.gui.settings.startup import FirstStart


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[Backend]:
    """Return a seeded backend (closed afterwards)."""
    with closing(Backend(seeded_ports(tmp_path))) as created:
        yield created


def _failed_keyring() -> CredentialsStatusResponse:
    """Return the status of a keyring that failed while reading: no key found, yet it may hold one."""
    key = CredentialStatus(name="runpod_api_key", environment_variable="RUNPOD_API_KEY", source="missing")
    return CredentialsStatusResponse(keyring="failed", credentials=(key,), runpod_key_set=True, r2_set=True)


def test_a_failed_keyring_offers_the_cloud_setup(backend: Backend) -> None:
    """The key counts as set: the cloud setup can be checked, and the start-up checks it."""
    deferred = Deferred(Scripted(backend, {"credentials_status": _failed_keyring()}))
    accounts = AccountsViewModel(deferred, AreaHooks())
    cloud = CloudSetupViewModel(deferred, Activity(deferred), accounts)
    accounts.load()
    deferred.answer()
    proceeded: list[bool] = []
    FirstStart(accounts, cloud, lambda _view: None).cloud_step(lambda: proceeded.append(True))
    assert (cloud.key_set, deferred.actions(), proceeded) == (True, ["cloud_setup_status"], [])
