from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from clients.api import CloudClient
from clients.relay import AdbRelay

connections: dict[str, tuple[CloudClient, AdbRelay]] = {}


async def disconnect(lease_id: str):
    connection = connections.pop(lease_id, None)
    if connection is not None:
        client, relay = connection
        await relay.__aexit__(None, None, None)
        await client.close()


@asynccontextmanager
async def lifespan(_):
    try:
        yield
    finally:
        lease_ids = list(connections)
        for lease_id in lease_ids:
            await disconnect(lease_id)


server = MCPServer("Agent Device Cloud", instructions="Provision private Android devices. Release each lease when finished. Devices are erased between tenants. connect_device establishes a local ADB tunnel for an active lease.", lifespan=lifespan)


async def call(method: str, path: str, body=None):
    client = CloudClient.from_env()
    try:
        return await client.request(method, path, body)
    finally:
        await client.close()


@server.tool()
async def provision_device(name: str, purpose: str = "development") -> dict:
    """Request a fresh device. Poll get_device until status is active; provisioning can take several minutes."""
    client = CloudClient.from_env()
    try:
        return await client.provision(name, purpose)
    finally:
        await client.close()


@server.tool()
async def get_device(lease_id: str) -> dict:
    """Inspect allocation progress and connection information."""
    return await call("GET", f"/v2/leases/{lease_id}")


@server.tool()
async def list_devices() -> dict:
    """List the signed-in account's device leases."""
    return await call("GET", "/v2/leases")


@server.tool()
async def release_device(lease_id: str) -> dict:
    """End a lease and erase its Android data before pool reuse."""
    await disconnect(lease_id)
    return await call("DELETE", f"/v2/leases/{lease_id}")


@server.tool()
async def connect_device(lease_id: str) -> dict:
    """Start a localhost-only ADB bridge on this MCP server's machine. Run the returned adb command locally."""
    if lease_id in connections:
        _, relay = connections[lease_id]
        return {"serial": relay.serial, "command": f"adb connect {relay.serial}"}
    client = CloudClient.from_env()
    try:
        lease = await client.request("GET", f"/v2/leases/{lease_id}")
        if lease["status"] != "active":
            raise ValueError("The device must be active before connecting")
        relay = AdbRelay(client, lease_id)
        await relay.__aenter__()
    except BaseException:
        await client.close()
        raise
    connections[lease_id] = (client, relay)
    return {"serial": relay.serial, "command": f"adb connect {relay.serial}"}


@server.tool()
async def get_usage() -> dict:
    """Read this account's usage seconds, minutes and estimated cost."""
    return await call("GET", "/v1/usage")


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
