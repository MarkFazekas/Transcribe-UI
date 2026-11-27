"""Test wiring of the fake Runpod API: clock, credentials, ports per parity leg."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from pydantic import SecretStr

from support.credentials import MemoryKeyring
from support.fakes import FIXED_NOW, seeded_ports
from support.runpod_api import FAKE_API_KEY, FakeRunpodApi, FakeSettings
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import CancelFlag, Ports
from transcribe_ui.credentials.constants import KEYRING_SERVICE
from transcribe_ui.credentials.store import CredentialStore
from transcribe_ui.runpod.access import RunpodAccess
from transcribe_ui.runpod.classes import SetupCloudRequest
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.timing import Stopped, never_stopped

# Distinctive secret values: no response, log line or error may contain any of them.
R2_PRIVATE = "r2-secret-access-key-MUST-NOT-LEAK"
R2_KEY_ID = "r2-access-key-id-MUST-NOT-LEAK"
R2_ENDPOINT = "https://account-MUST-NOT-LEAK.r2.cloudflarestorage.com"
R2_BUCKET = "bucket-must-not-leak"
# Every secret value of the test credentials, the Runpod key included.
SECRET_VALUES = (R2_PRIVATE, R2_KEY_ID, R2_ENDPOINT, R2_BUCKET, FAKE_API_KEY)
# The R2 credentials the tests store (credential store names).
TEST_R2: Mapping[str, str] = MappingProxyType(
    {
        "r2_endpoint_url": R2_ENDPOINT,
        "r2_bucket": R2_BUCKET,
        "r2_access_key_id": R2_KEY_ID,
        "r2_secret_access_key": R2_PRIVATE,
    },
)
# Only the Runpod API key.
RUNPOD_ONLY: Mapping[str, str] = MappingProxyType({"runpod_api_key": FAKE_API_KEY})
# Every credential: the R2 ones and the Runpod key.
FULL_CREDENTIALS: Mapping[str, str] = MappingProxyType({**TEST_R2, **RUNPOD_ONLY})
# No credential at all.
NO_CREDENTIALS: Mapping[str, str] = MappingProxyType({})


def credential_store(stored: Mapping[str, str]) -> CredentialStore:
    """Return a credential store whose in-memory keyring holds these credentials (no environment)."""
    keyring = MemoryKeyring({(KEYRING_SERVICE, name): text for name, text in stored.items()})
    return CredentialStore(lambda: keyring, {})


@dataclass
class ManualClock:
    """A clock the test moves forward."""

    moment: datetime = FIXED_NOW

    def now(self) -> datetime:
        """Return the current moment."""
        return self.moment

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward."""
        self.moment += delta


@dataclass
class WaitRecorder:
    """Records the waits between attempts instead of sleeping; may run a hook at each wait."""

    delays: list[float] = field(default_factory=list)
    on_wait: Callable[[], None] | None = None

    def __call__(self, seconds: float, _stopped: Stopped) -> None:
        """Record the wait."""
        self.delays.append(seconds)
        if self.on_wait is not None:
            self.on_wait()


@dataclass
class Cloud:
    """A fake Runpod API with the backend and client wired to it."""

    clock: ManualClock = field(default_factory=ManualClock)
    # Credentials in the keyring, by credential store name.
    credentials: Mapping[str, str] = FULL_CREDENTIALS
    settings: FakeSettings = field(default_factory=FakeSettings)
    sleeps: WaitRecorder = field(default_factory=WaitRecorder)
    # Folder of the local state of `set_up()`'s backend.
    root: Path | None = None
    api: FakeRunpodApi = field(init=False)

    def __post_init__(self) -> None:
        """Create the fake API on the clock."""
        self.api = FakeRunpodApi(self.clock.now, settings=self.settings)

    def access(self) -> RunpodAccess:
        """Return the Runpod port over the fake."""
        transport = self.api.transport()
        return RunpodAccess(transport=transport, wait=self.sleeps, now=self.clock.now)

    def ports(self, root: Path) -> Ports:
        """Return seeded ports (local state under `root`) whose Runpod port is the fake."""
        return replace(seeded_ports(root), runpod=self.access(), credentials=credential_store(self.credentials))

    def backend(self, root: Path) -> Backend:
        """Return a backend over the fake (its requests here never open the database)."""
        return Backend(self.ports(root))

    def set_up(self, cancel: CancelFlag | None = None) -> Response:
        """Run `setup_cloud` through a backend over the fake (it never opens the database)."""
        assert self.root is not None, "set_up() needs a Cloud with a root"
        return self.backend(self.root).execute(SetupCloudRequest(action="setup_cloud"), cancel=cancel)

    def client(self, stopped: Stopped = never_stopped) -> RunpodClient:
        """Return a client over the fake with the test key."""
        timing = self.access().timing(stopped)
        return RunpodClient(SecretStr(FAKE_API_KEY), transport=self.api.transport(), timing=timing)


@dataclass
class CloudLegs:
    """Ports factory for the parity runner: a fresh fake Runpod API per leg, seeded alike."""

    seed: Callable[[FakeRunpodApi], None] | None = None
    # Credentials in the keyring, by credential store name.
    credentials: Mapping[str, str] = FULL_CREDENTIALS
    # The clouds of the legs, in creation order (backend leg first).
    clouds: list[Cloud] = field(default_factory=list)

    def __call__(self, root: Path) -> Ports:
        """Return the ports of one leg."""
        cloud = Cloud(credentials=self.credentials)
        if self.seed is not None:
            self.seed(cloud.api)
        self.clouds.append(cloud)
        return cloud.ports(root)
