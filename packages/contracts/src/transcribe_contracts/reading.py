"""Read stored objects: refuse another major version first, validate, then match the key."""

import json
from typing import Any, NoReturn

from pydantic import BaseModel, ValidationError

from transcribe_contracts.classes import BatchSummary, JobResult, Manifest, RunLease, RunStatus, schema_major
from transcribe_contracts.constants import JSON_SUFFIX, SCHEMA_MAJOR
from transcribe_contracts.errors import ContractError, UnsupportedSchemaError
from transcribe_contracts.layout import (
    batch_id_from_manifest_key,
    done_key,
    parse_result_key,
    result_json_key,
    run_lease_key,
    run_status_key,
)


def read_manifest(key: str, body: bytes | str) -> Manifest:
    """Read `batch_metadata/<batch_id>.json`; its batch id must match the key.

    Raises:
        UnsupportedSchemaError: The object has another schema major version.
        ContractError: The object is invalid or does not match its key.
    """
    manifest = _validate(Manifest, body)
    if batch_id_from_manifest_key(key) != manifest.batch_id:
        _mismatch(key, manifest.batch_id)
    return manifest


def read_job_result(key: str, body: bytes | str) -> JobResult:
    """Read `<batch_id>/results/<item_id>.<language>.json`; batch, item and language must match.

    Raises:
        UnsupportedSchemaError: The object has another schema major version.
        ContractError: The object is invalid or does not match its key.
    """
    job_result = _validate(JobResult, body)
    parts = parse_result_key(key)
    found = (job_result.batch_id, job_result.item_id, job_result.language, JSON_SUFFIX)
    if parts is None or tuple(parts) != found:
        _mismatch(key, _expected_key(job_result))
    return job_result


def read_batch_summary(key: str, body: bytes | str) -> BatchSummary:
    """Read `<batch_id>/_done.json`; its batch id must match the key.

    Raises:
        UnsupportedSchemaError: The object has another schema major version.
        ContractError: The object is invalid or does not match its key.
    """
    summary = _validate(BatchSummary, body)
    if key != done_key(summary.batch_id):
        _mismatch(key, summary.batch_id)
    return summary


def read_run_status(key: str, body: bytes | str) -> RunStatus:
    """Read `runs/<run_id>/status.json`; its run id must match the key.

    Raises:
        UnsupportedSchemaError: The object has another schema major version.
        ContractError: The object is invalid or does not match its key.
    """
    status = _validate(RunStatus, body)
    if key != run_status_key(status.run_id):
        _mismatch(key, status.run_id)
    return status


def read_run_lease(key: str, body: bytes | str) -> RunLease:
    """Read `runs/<run_id>/launch.json`; its run id must match the key.

    Raises:
        UnsupportedSchemaError: The object has another schema major version.
        ContractError: The object is invalid or does not match its key.
    """
    lease = _validate(RunLease, body)
    if key != run_lease_key(lease.run_id):
        _mismatch(key, lease.run_id)
    return lease


def _validate[ModelT: BaseModel](model: type[ModelT], body: bytes | str) -> ModelT:
    raw = _load_object(body)
    _check_major(raw.get("schema_version"))
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        msg = f"invalid {model.__name__}: {error}"
        raise ContractError(msg) from error


def _load_object(body: bytes | str) -> dict[str, Any]:
    try:
        loaded = json.loads(body)
    except ValueError as error:
        msg = "object is not valid JSON"
        raise ContractError(msg) from error
    if not isinstance(loaded, dict):
        msg = "object is not a JSON object"
        raise ContractError(msg)
    return loaded


def _check_major(version: object) -> None:
    if not isinstance(version, str):
        msg = "schema_version is missing"
        raise ContractError(msg)
    try:
        major = schema_major(version)
    except ValueError as error:
        raise ContractError(str(error)) from error
    if major != SCHEMA_MAJOR:
        msg = f"schema version {version} is not supported (supported major: {SCHEMA_MAJOR})"
        raise UnsupportedSchemaError(msg)


def _expected_key(job_result: JobResult) -> str:
    return result_json_key(job_result.batch_id, job_result.item_id, job_result.language)


def _mismatch(key: str, found: str) -> NoReturn:
    msg = f"object at {key!r} does not belong there (content says {found!r})"
    raise ContractError(msg)
