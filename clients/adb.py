import asyncio
from contextlib import suppress

from clients.api import CloudError


async def adb(*arguments):
    process = await asyncio.create_subprocess_exec("adb", *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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


async def connect_adb(serial):
    await adb("connect", serial)
    state = await adb("-s", serial, "get-state")
    if state != "device":
        raise CloudError(f"ADB device is not ready: {state}")
