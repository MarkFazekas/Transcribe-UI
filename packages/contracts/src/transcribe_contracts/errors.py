"""Exceptions raised when a stored object breaks the contract."""


class ContractError(ValueError):
    """A stored object is invalid or does not match its key."""


class UnsupportedSchemaError(ContractError):
    """A stored object has a schema major version this code does not support."""
