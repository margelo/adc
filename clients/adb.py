import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from clients.api import CloudError


async def adb(*arguments, env=None):
    process = await asyncio.create_subprocess_exec(
        "adb", *arguments, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            stdout, stderr = await process.communicate()
    except BaseException as error:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process.wait()
        if isinstance(error, TimeoutError):
            raise CloudError("adb timed out") from error
        raise
    output = stdout.decode()
    if process.returncode:
        message = stderr.decode()
        message = message.strip()
        if message == "":
            message = output.strip()
        raise CloudError(f"adb failed: {message}")
    return output.strip()


async def connect_adb(serial, env=None):
    connected = await adb("connect", serial, env=env)
    state = await adb("-s", serial, "get-state", env=env)
    if state != "device":
        raise CloudError(f"ADB device is not ready: {state}")
    if env is not None:
        features = await adb("-s", serial, "features", env=env)
        if "delayed_ack" not in features.splitlines():
            raise CloudError("This Android device did not negotiate ADB Burst (delayed_ack)")
    return connected


async def wait_for_adb_server(process, socket_path, log_path):
    try:
        async with asyncio.timeout(5):
            while process.returncode is None:
                try:
                    _, writer = await asyncio.open_unix_connection(socket_path)
                except (FileNotFoundError, ConnectionRefusedError):
                    await asyncio.sleep(0.05)
                else:
                    writer.close()
                    await writer.wait_closed()
                    return
    except TimeoutError as error:
        raise CloudError("Timed out starting the ADB Burst server") from error
    details = log_path.read_text(errors="replace")
    message = details.strip()
    raise CloudError(f"ADB Burst server exited with status {process.returncode}: {message}")


@asynccontextmanager
async def adb_environment(burst):
    if burst is False:
        yield None
        return

    # Use a short path so the socket address fits even on macOS.
    with TemporaryDirectory(prefix="adc-adb-", dir="/tmp") as directory:
        root = Path(directory)
        socket_path = root / "adb.sock"
        log_path = root / "adb.log"
        server_socket = f"localfilesystem:{socket_path}"
        environment = os.environ.copy()
        environment.update(ADB_SERVER_SOCKET=server_socket, ADB_BURST_MODE="1", ADB_USB="0", ADB_EMU="0", ADB_MDNS="0")
        with log_path.open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                "adb", "-L", server_socket, "server", "nodaemon", env=environment,
                stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=asyncio.subprocess.STDOUT,
            )
        try:
            await wait_for_adb_server(process, socket_path, log_path)
            features = await adb("host-features", env=environment)
            if "delayed_ack" not in features.split(","):
                raise CloudError("This adb does not support Burst mode; update Android SDK Platform-Tools to 36.0.0 or newer")
            yield environment
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    async with asyncio.timeout(5):
                        await process.wait()
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
