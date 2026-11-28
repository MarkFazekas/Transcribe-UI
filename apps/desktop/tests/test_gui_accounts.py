"""Accounts, cloud setup, about and the first start in the settings area's view-models (plan m6 M6.3).

Credentials never reach display data, the log or a golden file: the sentinel values of
`support.credentials` are typed in and searched for (the `no_credential_logs` fixture checks
the log).
"""

from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from types import MappingProxyType

import pytest

from support.credentials import (
    NEVER_SHOWN,
    TEST_ACCESS_KEY_ID,
    TEST_BUCKET,
    TEST_ENDPOINT,
    TEST_RUNPOD_KEY,
    TEST_SECRET,
    NoKeyring,
    moto_bucket,
    store_request,
)
from support.fakes import seeded_ports
from support.golden import GOLDEN_DIR
from support.gui_settings import Heard, Recorded, Scripted
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorBody, ErrorResponse
from transcribe_ui.credentials.classes import StoreCredentialsRequest
from transcribe_ui.credentials.store import CredentialStore
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.settings.about import AboutViewModel, window_title
from transcribe_ui.gui.settings.account_texts import credential_hint
from transcribe_ui.gui.settings.accounts import AccountsViewModel
from transcribe_ui.gui.settings.cloud_setup import CloudSetupViewModel
from transcribe_ui.gui.settings.startup import FirstStart

pytestmark = pytest.mark.usefixtures("no_credential_logs")

BUCKET = "r2_bucket"
RUNPOD_KEY = "runpod_api_key"
# The R2 values as the user types them, by credential name.
R2_TYPED = MappingProxyType(
    {
        "r2_endpoint_url": TEST_ENDPOINT,
        BUCKET: TEST_BUCKET,
        "r2_access_key_id": TEST_ACCESS_KEY_ID,
        "r2_secret_access_key": TEST_SECRET,
    },
)


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[Backend]:
    """Return a seeded backend with an empty in-memory keyring (closed afterwards)."""
    with closing(Backend(seeded_ports(tmp_path))) as created:
        yield created


@dataclass(frozen=True)
class Area:
    """The accounts, cloud setup and first start view-models over one executor, and what they report."""

    executor: Recorded
    heard: Heard
    accounts: AccountsViewModel
    cloud: CloudSetupViewModel
    first_start: FirstStart
    # The steps the wizard was opened at, and the start-up steps that proceeded.
    wizard: list[str] = field(default_factory=list)
    proceeded: list[str] = field(default_factory=list)

    def start(self) -> None:
        """Run the area's start-up steps."""
        self.first_start.credentials_step(partial(self.proceeded.append, "credentials"))
        self.first_start.cloud_step(partial(self.proceeded.append, "cloud"))

    def shown(self) -> str:
        """Return every display data of the area as text."""
        shown = self.accounts.shown
        return repr((shown, shown.rows(), self.cloud.rows(), self.cloud.message, self.heard.errors))

    def typed(self) -> list[str]:
        """Return the credentials typed into and not stored yet."""
        return [row.name for row in self.accounts.shown.rows() if row.typed]


def _area(runner: Backend | Scripted) -> Area:
    executor = Recorded(runner)
    heard = Heard()
    accounts = AccountsViewModel(executor, heard.hooks())
    cloud = CloudSetupViewModel(executor, Activity(executor), accounts)
    wizard: list[str] = []
    first_start = FirstStart(accounts, cloud, wizard.append)
    return Area(executor, heard, accounts, cloud, first_start, wizard)


def _assert_nothing_shown(area: Area) -> None:
    shown = area.shown()
    assert not any(secret in shown for secret in NEVER_SHOWN)


def test_typed_credentials_are_stored_not_shown(backend: Backend) -> None:
    """Save sends the fields typed into; the fields are emptied and show where the values are now."""
    area = _area(backend)
    area.accounts.load()
    assert ({row.source for row in area.accounts.shown.rows()}, area.accounts.shown.can_save) == ({"Not set"}, False)
    area.accounts.type_value(BUCKET, TEST_BUCKET)
    area.accounts.type_value(RUNPOD_KEY, TEST_RUNPOD_KEY)
    area.accounts.type_value("r2_endpoint_url", "typed, then deleted")
    area.accounts.type_value("r2_endpoint_url", "")
    assert area.typed() == [BUCKET, RUNPOD_KEY]
    _assert_nothing_shown(area)
    area.accounts.save()
    stored = [request for request in area.executor.sent if isinstance(request, StoreCredentialsRequest)]
    assert [request.to_store() for request in stored] == [{BUCKET: TEST_BUCKET, RUNPOD_KEY: TEST_RUNPOD_KEY}]
    stored_now = {row.name for row in area.accounts.shown.rows() if row.source == "Stored in the keyring"}
    assert stored_now == {BUCKET, RUNPOD_KEY}
    assert (area.accounts.shown.message, area.typed(), area.heard.errors) == ("Saved.", [], [])
    _assert_nothing_shown(area)


def test_refused_credentials_keep_what_was_typed(backend: Backend) -> None:
    """A value the request's validator refuses is not sent; its field says how to fix it."""
    area = _area(backend)
    area.accounts.load()
    area.accounts.type_value(BUCKET, "Not_A_Bucket")
    area.accounts.type_value("r2_access_key_id", TEST_ACCESS_KEY_ID)
    area.accounts.save()
    assert ("store_credentials" in area.executor.actions(), area.accounts.shown.can_save) == (False, True)
    assert area.accounts.shown.errors == {BUCKET: credential_hint(BUCKET)}
    assert area.typed() == [BUCKET, "r2_access_key_id"]
    _assert_nothing_shown(area)
    area.accounts.type_value(BUCKET, TEST_BUCKET)
    assert not area.accounts.shown.errors


def test_a_keyring_failure_keeps_what_was_typed(tmp_path: Path) -> None:
    """Without a keyring, Save shows `keyring_unavailable`; the page says to use the environment variables."""
    ports = replace(seeded_ports(tmp_path), credentials=CredentialStore(NoKeyring, {}))
    with closing(Backend(ports)) as backend:
        area = _area(backend)
        area.accounts.load()
        area.accounts.type_value(RUNPOD_KEY, TEST_RUNPOD_KEY)
        area.accounts.save()
    status = area.accounts.shown.status
    assert status is not None
    assert ([view.code for view in area.heard.errors], status.keyring) == (["keyring_unavailable"], "unavailable")
    assert area.typed() == [RUNPOD_KEY]
    _assert_nothing_shown(area)


def test_the_credential_test_reports_each_service(backend: Backend) -> None:
    """Test tries R2 and Runpod; each result is the catalog text of its code."""
    with moto_bucket():
        assert backend.execute_json(store_request()).status == "ok"
        area = _area(backend)
        area.accounts.test()
    checks = area.accounts.shown.checks
    assert [(check.service, check.outcome_label) for check in checks] == [("R2 storage", "OK"), ("Runpod", "OK")]
    assert (checks[0].text, area.accounts.shown.checking) == (
        "R2 accepted the credentials and the bucket can be listed.",
        False,
    )
    _assert_nothing_shown(area)


def test_missing_r2_opens_the_wizard(backend: Backend) -> None:
    """Missing R2 values open the wizard at accounts and show the notice; without a Runpod key no cloud check."""
    area = _area(backend)
    area.start()
    assert (area.wizard, area.proceeded, area.first_start.notice) == (
        ["accounts"],
        ["credentials", "cloud"],
        "accounts",
    )
    assert ("cloud_setup_status" in area.executor.actions(), area.cloud.key_set) == (False, False)
    for name, typed in R2_TYPED.items():
        area.accounts.type_value(name, typed)
    area.accounts.save()
    # R2-only (architecture §3.9): complete without the Runpod key.
    assert area.first_start.notice is None
    _assert_nothing_shown(area)


def test_needed_cloud_setup_opens_the_wizard(backend: Backend) -> None:
    """With every credential, missing cloud objects open the wizard there; Set up makes the setup ready."""
    assert backend.execute_json(store_request()).status == "ok"
    area = _area(backend)
    area.start()
    states = {row.state for row in area.cloud.rows()}
    assert (area.wizard, area.first_start.notice, states) == (["cloud_setup"], "cloud_setup", {"Missing"})
    area.cloud.set_up()
    assert area.executor.actions()[-2:] == ["setup_cloud", "cloud_setup_status"]
    assert (area.cloud.message, {row.state for row in area.cloud.rows()}) == (
        "Cloud setup done: 6 created, 0 updated, 0 unchanged.",
        {"Ready"},
    )
    # The wizard opens once per session.
    area.start()
    assert (area.cloud.error, area.first_start.notice, area.wizard) == (None, None, ["cloud_setup"])


def test_a_failed_cloud_check_shows_a_notice(backend: Backend) -> None:
    """A cloud error from `cloud_setup_status` shows the notice with Retry, not the wizard; Hide hides it."""
    assert backend.execute_json(store_request()).status == "ok"
    unreachable = ErrorResponse(
        action="cloud_setup_status",
        error=ErrorBody(code="cloud_unreachable", message="Runpod cannot be reached."),
    )
    area = _area(Scripted(backend, MappingProxyType({"cloud_setup_status": unreachable})))
    area.start()
    assert (area.wizard, area.first_start.notice) == ([], "cloud_error")
    error = area.cloud.error
    assert error is not None
    assert error.message == "Runpod cannot be reached or answered unexpectedly. Try again later."
    area.first_start.hide()
    assert area.first_start.notice is None


def test_about_shows_the_versions(backend: Backend, tmp_path: Path) -> None:
    """`get_app_info` gives the versions; the window title gets the app's."""
    about = AboutViewModel(Recorded(backend), tmp_path / "log")
    assert window_title(about.version) == "Transcribe-UI"
    about.load()
    assert window_title(about.version) == f"Transcribe-UI {about.version}"
    assert about.version


def test_no_settings_golden_holds_a_credential() -> None:
    """The settings area's golden files hold no credential."""
    goldens = sorted(GOLDEN_DIR.glob("gui_settings_*.json"))
    found = [golden.name for golden in goldens for secret in NEVER_SHOWN if secret in golden.read_text("utf-8")]
    assert (bool(goldens), found) == (True, [])
