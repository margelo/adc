import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
from contextlib import asynccontextmanager, suppress

from clients.adb import adb, connect_adb
from clients.api import CloudClient, CloudError
from clients.relay import AdbRelay


class StaleConnection(CloudError):
    pass


def connection_path(client, lease_id):
    directory = os.environ.get("ADC_STATE_DIR")
    if directory:
        root = Path(directory)
    else:
        root = Path.home() / ".local/state/agent-device-cloud/connections"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = f"{client.base_url}\0{lease_id}"
    encoded = identity.encode()
    digest = hashlib.sha256(encoded)
    key = digest.hexdigest()
    return root / f"{key}.json"


@asynccontextmanager
async def connection_lock(path):
    lock_path = path.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_connection(path):
    try:
        contents = path.read_text()
    except FileNotFoundError:
        return None
    return json.loads(contents)


async def control(state, action):
    async with asyncio.timeout(35):
        reader, writer = await asyncio.open_connection("127.0.0.1", state["control_port"])
        try:
            request = {"token": state["token"], "action": action}
            rendered = json.dumps(request)
            payload = (rendered + "\n").encode()
            writer.write(payload)
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise CloudError("The local ADB connection stopped; run adc connect again")
            response = json.loads(line)
            if response.get("code") == "invalid_token":
                raise StaleConnection("The saved local connection belongs to a stopped process")
            if "error" in response:
                raise CloudError(response["error"])
            return response
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()


async def stop_process(process):
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        completion = asyncio.to_thread(process.wait, timeout=20)
        await completion
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            process.kill()
        completion = asyncio.to_thread(process.wait)
        await completion


async def connect_device(client, lease_id, port=0):
    path = connection_path(client, lease_id)
    process = None
    try:
        async with connection_lock(path):
            await require_active(client, lease_id)
            state = read_connection(path)
            if state is not None:
                try:
                    await control(state, "status")
                except (ConnectionRefusedError, StaleConnection):
                    path.unlink(missing_ok=True)
                else:
                    if port and state["serial"] != f"127.0.0.1:{port}":
                        raise CloudError(f"Already connected at {state['serial']}; disconnect before changing the port")
                    return await control(state, "connect")
            token = secrets.token_urlsafe(32)
            environment = os.environ.copy()
            environment.update(ADC_API_URL=client.base_url, ADC_API_KEY=client.api_key, ADC_CONNECTION_TOKEN=token)
            log_path = path.with_suffix(".log")
            descriptor = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            port_argument = str(port)
            state_argument = str(path)
            with os.fdopen(descriptor, "w") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "clients.connections", lease_id, "--port", port_argument, "--state-file", state_argument],
                    env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                )
            async with asyncio.timeout(40):
                while True:
                    if process.poll() is not None:
                        message = log_path.read_text()
                        message = message.strip()
                        raise CloudError(f"Could not connect ADB: {message}")
                    state = read_connection(path)
                    if state is not None and state["token"] == token:
                        return await control(state, "status")
                    await asyncio.sleep(0.05)
    except BaseException as error:
        if process is not None:
            await stop_process(process)
        if isinstance(error, TimeoutError):
            raise CloudError("Timed out starting the local ADB connection") from error
        raise


async def disconnect_device(client, lease_id):
    path = connection_path(client, lease_id)
    async with connection_lock(path):
        state = read_connection(path)
        if state is None:
            return False
        try:
            await control(state, "disconnect")
        except (ConnectionRefusedError, StaleConnection):
            path.unlink(missing_ok=True)
            return False
    async with asyncio.timeout(20):
        while True:
            current = read_connection(path)
            if current is None or current["token"] != state["token"]:
                return True
            await asyncio.sleep(0.05)


async def watch_lease(client, lease_id, stopped):
    try:
        while True:
            lease = await client.request("GET", f"/v2/leases/{lease_id}")
            if lease["status"] != "active":
                return
            await asyncio.sleep(1)
    except Exception as error:
        logger = logging.getLogger(__name__)
        logger.warning("Lease monitoring stopped: %s", error)
    finally:
        stopped.set()


async def require_active(client, lease_id):
    lease = await client.request("GET", f"/v2/leases/{lease_id}")
    if lease["status"] != "active":
        raise CloudError("Only active leases can connect")
    return lease


async def serve_connection(lease_id, port, path, token):
    client = CloudClient.from_env()
    stopped = asyncio.Event()
    handlers = set()
    state = None
    server = None
    closing = False
    try:
        async with AdbRelay(client, lease_id, port) as relay:
            async def handle(reader, writer):
                task = asyncio.current_task()
                handlers.add(task)
                disconnect = False
                try:
                    if closing:
                        return
                    try:
                        async with asyncio.timeout(35):
                            line = await reader.readline()
                            request = json.loads(line)
                            authenticated = secrets.compare_digest(request["token"], token)
                            if authenticated is False:
                                raise StaleConnection("Invalid local connection token")
                            action = request["action"]
                            if action == "disconnect":
                                disconnect = True
                            elif stopped.is_set():
                                raise CloudError("The ADB connection is closing")
                            elif action == "connect":
                                await connect_adb(relay.serial)
                                if stopped.is_set():
                                    raise CloudError("The ADB connection is closing")
                            elif action != "status":
                                raise CloudError("Unknown local connection action")
                            response = {"serial": relay.serial}
                    except StaleConnection as error:
                        response = {"error": str(error), "code": "invalid_token"}
                    except Exception as error:
                        response = {"error": str(error)}
                    rendered = json.dumps(response)
                    payload = (rendered + "\n").encode()
                    writer.write(payload)
                    await writer.drain()
                    if disconnect:
                        stopped.set()
                finally:
                    writer.close()
                    with suppress(OSError):
                        await writer.wait_closed()
                    handlers.discard(task)

            watcher = None
            try:
                await connect_adb(relay.serial)
                lease = await require_active(client, lease_id)
                relay.publish_label(lease["name"])
                server = await asyncio.start_server(handle, "127.0.0.1", 0)
                address = server.sockets[0].getsockname()
                state = {"serial": relay.serial, "control_port": address[1], "token": token}
                rendered = json.dumps(state)
                temporary = path.with_suffix(".tmp")
                descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    output.write(rendered)
                temporary.replace(path)
                monitoring = watch_lease(client, lease_id, stopped)
                watcher = asyncio.create_task(monitoring)
                await stopped.wait()
            finally:
                stopped.set()
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                with suppress(CloudError, OSError):
                    await adb("disconnect", relay.serial)
    finally:
        await client.close()
        if server is not None:
            async with connection_lock(path):
                closing = True
                server.close()
                tasks = list(handlers)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await server.wait_closed()
                current = read_connection(path)
                if current is not None and current["token"] == token:
                    path.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lease_id")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.pop("ADC_CONNECTION_TOKEN")

    async def run():
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        for signum in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(signum, task.cancel)
        await serve_connection(args.lease_id, args.port, args.state_file, token)

    with asyncio.Runner() as runner:
        try:
            operation = run()
            runner.run(operation)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            print(f"adc connection: {error}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
