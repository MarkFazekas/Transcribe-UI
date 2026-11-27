"""The external adapter types `Ports` holds, as aliases.

`api/ports.py` imports these aliases for type checking only, so importing the ports does not
load keyring, boto3, httpx or the media tools (and `media.ports`, which uses the ports'
`CancelToken`, causes no import cycle).
"""

from transcribe_ui.credentials.store import CredentialStore
from transcribe_ui.media.ports import MediaTools
from transcribe_ui.runpod.access import RunpodAccess
from transcribe_ui.storage.client import R2ClientFactory

# The credential store (keyring first, then the environment).
type CredentialsPort = CredentialStore
# The ffprobe/ffmpeg adapter.
type MediaPort = MediaTools
# Builds the S3 client for R2 from the credentials.
type R2ClientsPort = R2ClientFactory
# The Runpod API: transport, retry waits and clock.
type RunpodPort = RunpodAccess
