"""Base model and path-typed fields shared by every request and response model (architecture §3.1).

Kept apart from `classes.py`, so the sub-packages' request and response models can build on it
while `classes.py` joins them into the `Request`/`Response` unions.
"""

from collections.abc import Callable, Mapping
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer, SerializationInfo

from transcribe_contracts.classes import PathFlavor as ContractPathFlavor
from transcribe_contracts.constants import WHISPER_LANGUAGES
from transcribe_ui.api.constants import LOCAL_PATH_CONTEXT_KEY, PATH_FLAVOR_CONTEXT_KEY


def _rewritten(text: str, serialization: SerializationInfo, key: str) -> str:
    """Return the text passed through the rewrite function the serialization context holds."""
    context = serialization.context
    if not isinstance(context, Mapping):
        return text
    rewrite = context.get(key)
    if not callable(rewrite):
        return text
    return str(rewrite(text))


def _serialise_local_path(path: str, serialization: SerializationInfo) -> str:
    return _rewritten(path, serialization, LOCAL_PATH_CONTEXT_KEY)


def _serialise_path_flavor(flavor: str, serialization: SerializationInfo) -> str:
    return _rewritten(flavor, serialization, PATH_FLAVOR_CONTEXT_KEY)


# A path on this computer, as the OS reports it. The type marks the field for the golden-file
# normaliser, which passes a rewrite function in the serialization context; without one the
# value is written unchanged.
type LocalPath = Annotated[str, PlainSerializer(_serialise_local_path, return_type=str)]
# The path flavour of this computer; marked for the golden-file normaliser like `LocalPath`.
type PathFlavor = Annotated[ContractPathFlavor, PlainSerializer(_serialise_path_flavor, return_type=str)]


def _check_whisper_language(code: str) -> str:
    if code not in WHISPER_LANGUAGES:
        msg = f"{code!r} is not a language Whisper can transcribe"
        raise ValueError(msg)
    return code


# A language code of the Whisper language set (machine-independent, so every frontend agrees).
type WhisperLanguage = Annotated[str, AfterValidator(_check_whisper_language)]

# Signature of a serialization rewrite function (see `LocalPath`).
type Rewrite = Callable[[str], str]


class FrozenModel(BaseModel):
    """Base of the façade models: immutable, unknown fields refused.

    In the responses' (serialization) schema, fields with defaults are required: they are
    always written.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_serialization_defaults_required=True)
