"""Secret resolution from various backends.

Secrets are referenced in config by URI:
    env://VAR_NAME
    keyring://service/account
    vault://path/key
    awssm://secret-name
    gcpsm://projects/X/secrets/Y/versions/latest
    azurekv://vault.vault.azure.net/secret-name

The resolver is purposely lazy: backends are only imported when actually used,
so a user with only env:// secrets doesn't need to install boto3/azure-keyvault.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from touchstone.types import ConfigError


def resolve(ref: str) -> str:
    """Resolve a secret-scheme URI to a plaintext secret. Result is NEVER
    logged. Callers should hold the returned string only as long as needed."""

    parsed = urlparse(ref)
    scheme = parsed.scheme
    try:
        resolver = _RESOLVERS[scheme]
    except KeyError as e:
        raise ConfigError(f"unknown secret scheme: {scheme!r} in {ref!r}") from e
    value = resolver(parsed)
    if not value:
        raise ConfigError(f"secret resolved to empty value: {ref!r}")
    return value


def _env(parsed) -> str:
    var = parsed.netloc or parsed.path.lstrip("/")
    value = os.environ.get(var)
    if value is None:
        raise ConfigError(f"environment variable not set: {var}")
    return value


def _keyring(parsed) -> str:
    import keyring  # type: ignore[import-untyped]

    service = parsed.netloc
    account = parsed.path.lstrip("/")
    value = keyring.get_password(service, account)
    if not value:
        raise ConfigError(f"keyring secret not found: service={service!r} account={account!r}")
    return value


def _vault(parsed) -> str:
    # Implementation deferred — left as the canonical extension point.
    raise ConfigError(
        "Vault secret resolution requires the optional 'hvac' dependency. "
        "Install with: pip install touchstone-core[vault]"
    )


def _awssm(parsed) -> str:
    try:
        import boto3
    except ImportError as e:
        raise ConfigError(
            "AWS Secrets Manager requires boto3. Install with: pip install boto3"
        ) from e
    secret_id = parsed.netloc + parsed.path
    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_id)
    return resp.get("SecretString", "")


def _gcpsm(parsed) -> str:
    try:
        from google.cloud import secretmanager
    except ImportError as e:
        raise ConfigError(
            "GCP Secret Manager requires google-cloud-secret-manager."
        ) from e
    name = parsed.netloc + parsed.path
    client = secretmanager.SecretManagerServiceClient()
    resp = client.access_secret_version(name=name)
    return resp.payload.data.decode("utf-8")


def _azurekv(parsed) -> str:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as e:
        raise ConfigError(
            "Azure Key Vault requires azure-identity and azure-keyvault-secrets."
        ) from e
    vault_url = f"https://{parsed.netloc}"
    name = parsed.path.lstrip("/")
    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    return client.get_secret(name).value or ""


_RESOLVERS = {
    "env": _env,
    "keyring": _keyring,
    "vault": _vault,
    "awssm": _awssm,
    "gcpsm": _gcpsm,
    "azurekv": _azurekv,
}
