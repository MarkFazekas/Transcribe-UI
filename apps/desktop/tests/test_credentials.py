"""Flow tests of the credential requests (architecture §3.6; plan m5, M5.5).

Every request runs through the backend and the CLI (parity goldens). No credential value may
appear in a response, a progress event or a log record, whatever the request and its outcome.
"""

import dataclasses
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import pytest

from support.credentials import (
    NEVER_SHOWN,
    SENTINELS,
    TEST_BUCKET,
    TEST_ENDPOINT,
    LockedKeyring,
    NoKeyring,
    memory_store,
    moto_bucket,
    store_request,
)
from support.fakes import seeded_ports
from support.golden import assert_golden
from support.parity import Exchange, run_one, run_parity
from transcribe_ui.api.backend import Route, Routes
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import CancelFlag, DiscardProgress, RequestContext
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_INVALID_REQUEST, EXIT_OK
from transcribe_ui.credentials.classes import CheckCredentialsRequest, R2Credentials
from transcribe_ui.credentials.services import check_credentials, credentials_status, store_credentials
from transcribe_ui.credentials.store import CredentialStore, SecretBackend
from transcribe_ui.storage.client import CHECK_LIMITS, ClientLimits, r2_client

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

# Golden files are named after the action first: `.gitignore` drops `credentials*.json`.

STATUS = '{"action": "credentials_status"}'
CHECK = '{"action": "check_credentials"}'
# Every test checks the log records for credentials.
pytestmark = pytest.mark.usefixtures("no_credential_logs")
# A request handler of any request type.
type RequestHandler = Callable[[Any, RequestContext], Response]
# Handlers of the credential requests, for routes that swap the credential store.
HANDLERS: Mapping[str, RequestHandler] = MappingProxyType(
    {
        "store_credentials": store_credentials,
        "credentials_status": credentials_status,
        "check_credentials": check_credentials,
    },
)
# Environment of the no-keyring flows: the R2 credentials (padded, as pasted), no Runpod key.
R2_ENVIRONMENT = MappingProxyType(
    {
        "R2_ENDPOINT_URL": f"  {TEST_ENDPOINT}/ ",
        "R2_BUCKET": TEST_BUCKET,
        "R2_ACCESS_KEY_ID": SENTINELS[0],
        "R2_SECRET_ACCESS_KEY": SENTINELS[1],
    },
)


@pytest.fixture
def bucket() -> Iterator[None]:
    """Serve the test bucket from moto."""
    with moto_bucket():
        yield


def _assert_no_credentials(exchanges: list[Exchange]) -> None:
    for exchange in exchanges:
        text = exchange.golden()
        for secret in NEVER_SHOWN:
            assert secret not in text


@dataclasses.dataclass(frozen=True)
class WithStore:
    """A handler run with the credential store swapped in."""

    store: CredentialStore
    serve: RequestHandler

    def __call__(self, request: Any, context: RequestContext) -> Response:  # noqa: ANN401 -- any request type.
        """Run the handler on ports holding this store."""
        ports = dataclasses.replace(context.ports, credentials=self.store)
        return self.serve(request, dataclasses.replace(context, ports=ports))


@dataclasses.dataclass(frozen=True)
class StoreRoutes:
    """Routes factory: the credential requests on a store with this keyring and environment."""

    keyring: Callable[[], SecretBackend]
    environ: Mapping[str, str] = dataclasses.field(default_factory=dict)

    def __call__(self, _root: Path) -> Routes:
        """Return the routes of one leg (one store per leg)."""
        store = CredentialStore(self.keyring, self.environ)
        return {action: Route(run=WithStore(store, serve), needs_database=False) for action, serve in HANDLERS.items()}


@dataclasses.dataclass
class CountingFactory:
    """Keyring factory that counts its calls."""

    calls: int = 0

    def __call__(self) -> NoKeyring:
        """Count the call and return a backend."""
        self.calls += 1
        return NoKeyring()


def _locked_keyring() -> LockedKeyring:
    return LockedKeyring(allowed_writes=2)


@pytest.mark.usefixtures("bucket")
def test_store_status_and_check_flow(tmp_path: Path) -> None:
    """Credentials stored in the keyring are reported by name and source and pass the R2 check."""
    exchanges = run_parity(tmp_path, [STATUS, store_request(), STATUS, CHECK])
    assert {exchange.exit_code for exchange in exchanges} == {EXIT_OK}
    _assert_no_credentials(exchanges)
    names = ("status_credentials_empty", "store_credentials", "status_credentials_stored", "check_credentials_ok")
    for name, exchange in zip(names, exchanges, strict=True):
        assert_golden(f"{name}.json", exchange.golden())


def test_check_without_credentials_reports_missing(tmp_path: Path) -> None:
    """Without credentials the R2 check is "missing" and names the environment variables."""
    exchange = run_one(tmp_path, CHECK)
    assert exchange.exit_code == EXIT_OK
    assert_golden("check_credentials_missing.json", exchange.golden())


@pytest.mark.usefixtures("bucket")
def test_check_reports_unknown_bucket(tmp_path: Path) -> None:
    """A bucket R2 does not have fails the check with `r2_bucket_not_found`."""
    exchanges = run_parity(tmp_path, [store_request(r2_bucket="no-such-bucket"), CHECK])
    _assert_no_credentials(exchanges)
    assert '"r2_bucket_not_found"' in exchanges[1].response
    assert_golden("check_credentials_unknown_bucket.json", exchanges[1].golden())


def test_store_without_keyring_names_the_variables(tmp_path: Path) -> None:
    """With no keyring backend, storing fails with an error naming the environment variables."""
    exchange = run_one(tmp_path, store_request(), StoreRoutes(NoKeyring))
    assert exchange.exit_code == EXIT_ERROR
    _assert_no_credentials([exchange])
    assert_golden("store_credentials_no_keyring.json", exchange.golden())


@pytest.mark.usefixtures("bucket")
def test_environment_fallback_without_keyring(tmp_path: Path) -> None:
    """Without a keyring the environment variables are used, reported as such, and work."""
    exchanges = run_parity(tmp_path, [STATUS, CHECK], StoreRoutes(NoKeyring, R2_ENVIRONMENT))
    _assert_no_credentials(exchanges)
    assert '"outcome": "ok"' in exchanges[1].response
    assert_golden("status_credentials_environment.json", exchanges[0].golden())


def test_locked_keyring_reports_what_was_stored(tmp_path: Path) -> None:
    """A keyring failing mid-way gives `keyring_failed` listing the entries written before it."""
    exchange = run_one(tmp_path, store_request(), StoreRoutes(_locked_keyring))
    _assert_no_credentials([exchange])
    assert_golden("store_credentials_locked.json", exchange.golden())


def test_invalid_environment_credential_is_named(tmp_path: Path) -> None:
    """An environment credential of the wrong form is named with its source, never shown."""
    environ = {**R2_ENVIRONMENT, "R2_ACCESS_KEY_ID": f"{SENTINELS[0]} with spaces"}
    exchange = run_one(tmp_path, CHECK, StoreRoutes(NoKeyring, environ))
    _assert_no_credentials([exchange])
    assert_golden("check_credentials_invalid_environment.json", exchange.golden())


@pytest.mark.parametrize(
    ("name", "document"),
    [
        ("nothing", '{"action": "store_credentials"}'),
        ("endpoint", store_request(r2_endpoint_url=f"http://{SENTINELS[0]}.example")),
        ("bucket", store_request(r2_bucket=f"Bucket_{SENTINELS[0]}")),
        ("spaces", store_request(r2_secret_access_key=f"{SENTINELS[1]} {SENTINELS[1]}")),
    ],
)
def test_invalid_store_request_is_refused(tmp_path: Path, name: str, document: str) -> None:
    """An invalid `store_credentials` is refused on both legs without echoing any value."""
    exchange = run_one(tmp_path, document)
    assert exchange.exit_code == EXIT_INVALID_REQUEST
    _assert_no_credentials([exchange])
    assert_golden(f"store_credentials_invalid_{name}.json", exchange.golden())


def test_keyring_entry_wins_over_environment() -> None:
    """A keyring entry wins over its environment variable."""
    store = memory_store({"R2_BUCKET": "from-environment"})
    store.store({"r2_bucket": "from-keyring"})
    lookup = store.lookup()
    assert lookup.text("r2_bucket") == "from-keyring"
    assert lookup.source("r2_bucket") == "keyring"
    assert lookup.source("r2_secret_access_key") == "missing"


def test_environment_fills_what_the_keyring_lacks() -> None:
    """An environment variable serves a credential the keyring has no entry for (white space dropped)."""
    store = memory_store({"RUNPOD_API_KEY": "  environment-key  "})
    lookup = store.lookup()
    assert lookup.text("runpod_api_key") == "environment-key"
    assert lookup.source("runpod_api_key") == "environment"
    assert store.runpod_api_key().get_secret_value() == "environment-key"


def test_failing_keyring_falls_back_to_the_environment() -> None:
    """A keyring that cannot be read is reported as failed; the environment still serves."""
    store = CredentialStore(LockedKeyring, {"RUNPOD_API_KEY": "environment-key"})
    keyring, statuses = store.statuses()
    assert keyring == "failed"
    assert {status.name: status.source for status in statuses}["runpod_api_key"] == "environment"


def test_keyring_backend_is_built_once() -> None:
    """The keyring backend is created on first use and reused."""
    factory = CountingFactory()
    store = CredentialStore(factory, {})
    store.statuses()
    store.statuses()
    assert factory.calls == 1


@dataclasses.dataclass
class RecordingClients:
    """Client factory that records the limits it is asked for."""

    limits: list[ClientLimits] = dataclasses.field(default_factory=list)

    def __call__(self, credentials: R2Credentials, limits: ClientLimits) -> "S3Client":
        """Record the limits and build the real client."""
        self.limits.append(limits)
        return r2_client(credentials, limits)


@pytest.mark.usefixtures("bucket")
def test_check_uses_short_limits(tmp_path: Path) -> None:
    """`check_credentials` builds its client with 2 attempts and short timeouts."""
    clients = RecordingClients()
    store = CredentialStore(NoKeyring, R2_ENVIRONMENT)
    ports = dataclasses.replace(seeded_ports(tmp_path), credentials=store, r2_clients=clients)
    context = RequestContext(ports, DiscardProgress(), CancelFlag())
    response = check_credentials(CheckCredentialsRequest(action="check_credentials"), context)
    # The environment holds no Runpod key, so the Runpod check reports it missing.
    assert [(check.service, check.outcome) for check in response.checks] == [("r2", "ok"), ("runpod", "missing")]
    assert clients.limits == [CHECK_LIMITS]
    config: Any = r2_client(store.r2(), CHECK_LIMITS).meta.config  # botocore sets the options dynamically.
    assert config.retries == {"total_max_attempts": 2, "mode": "standard"}
    assert (config.connect_timeout, config.read_timeout) == (5, 10)
