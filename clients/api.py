import asyncio
import os
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx

from clients.credentials import CredentialError, load_credentials


DEFAULT_API_URL = "https://api.agentdevicecloud.xyz"


class CloudError(RuntimeError):
    pass


def normalize_api_url(value: str, setting="ADC_API_URL") -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CloudError(f"{setting} must be an HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise CloudError("Use HTTPS for remote API connections")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username is not None:
        raise CloudError(f"{setting} must contain only the origin")
    try:
        port = parsed.port
    except ValueError as error:
        raise CloudError(f"{setting} has an invalid port") from error
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (parsed.scheme, port) not in {("https", 443), ("http", 80)}:
        return f"{parsed.scheme}://{host}:{port}"
    return f"{parsed.scheme}://{host}"


def configured_api_url() -> str:
    value = os.environ.get("ADC_API_URL") or DEFAULT_API_URL
    return normalize_api_url(value)


class CloudClient:
    def __init__(self, base_url: str, api_key: str, transport=None, *, saved_login=False):
        self.base_url = normalize_api_url(base_url)
        self.api_key = api_key
        self.saved_login = saved_login
        headers = {"Authorization": f"Bearer {api_key}"}
        self.http = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=30, transport=transport)

    @classmethod
    def from_env(cls):
        base_url = configured_api_url()
        api_key = os.environ.get("ADC_API_KEY", "")
        if api_key:
            return cls(base_url, api_key)
        try:
            credentials = load_credentials(base_url)
        except CredentialError as error:
            message = str(error)
            raise CloudError(message) from error
        if credentials is None:
            raise CloudError("Run adc login or set ADC_API_KEY first")
        return cls(base_url, credentials.api_key, saved_login=True)

    async def close(self):
        await self.http.aclose()

    async def request(self, method: str, path: str, body=None):
        response = await self.http.request(method, path, json=body)
        if response.is_error:
            try:
                payload = response.json()
                detail = payload.get("detail", payload)
            except ValueError:
                detail = response.reason_phrase
            if response.status_code == 401 and self.saved_login:
                detail = f"{detail}\nRun adc login --switch to reconnect or choose another account."
            raise CloudError(f"API {response.status_code}: {detail}")
        return response.json()

    async def provision(self, name: str, purpose: str, idempotency_key: str | None = None):
        if idempotency_key is None:
            identifier = uuid.uuid4()
            idempotency_key = str(identifier)
        body = {"name": name, "purpose": purpose, "idempotency_key": idempotency_key}
        return await self.request("POST", "/v2/leases", body)

    async def release(self, lease_id: str):
        return await self.request("DELETE", f"/v2/leases/{lease_id}")

    async def wait_active(self, lease: dict, timeout_seconds: float = 600):
        deadline = time.monotonic() + timeout_seconds
        while lease["status"] == "queued":
            if time.monotonic() >= deadline:
                raise CloudError("Device provisioning timed out; inspect the lease and infrastructure events")
            await asyncio.sleep(1)
            lease_id = lease["id"]
            lease = await self.request("GET", f"/v2/leases/{lease_id}")
        if lease["status"] != "active":
            message = lease.get("error") or lease.get("failure_reason") or lease["status"]
            raise CloudError(f"Device is unavailable: {message}")
        return lease

    def websocket_url(self, lease_id: str):
        parsed = urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, f"/v2/leases/{lease_id}/adb", "", ""))
