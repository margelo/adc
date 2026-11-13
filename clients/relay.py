import asyncio
import json
import logging
import os
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from websockets.asyncio.client import connect

from clients.api import CloudClient


class AdbRelay:
    def __init__(self, client: CloudClient, lease_id: str, port: int = 0, *, booking_name: str | None = None):
        self.client = client
        self.lease_id = lease_id
        self.port = port
        self.booking_name = booking_name
        self.label_path: Path | None = None
        self.server = None
        self.closing = False
        self.tasks: set[asyncio.Task] = set()

    @property
    def serial(self):
        return f"127.0.0.1:{self.port}"

    async def __aenter__(self):
        self.closing = False
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", self.port)
        address = self.server.sockets[0].getsockname()
        self.port = address[1]
        try:
            if self.booking_name is not None:
                self.publish_label(self.booking_name)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    def publish_label(self, name: str):
        root = Path.home()
        directory = root / ".local/state/agent-device-cloud/adb-labels"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / f"{self.port}.json"
        pid = os.getpid()
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete_on_close=False) as output:
            json.dump({"name": name, "pid": pid}, output)
            output.close()
            staged = Path(output.name)
            staged.replace(path)
        self.label_path = path

    async def __aexit__(self, *_):
        self.closing = True
        try:
            if self.label_path is not None:
                self.label_path.unlink(missing_ok=True)
                self.label_path = None
        finally:
            self.server.close()
            tasks = list(self.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.server.wait_closed()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            if self.closing:
                return
            url = self.client.websocket_url(self.lease_id)
            headers = {"Authorization": f"Bearer {self.client.api_key}"}
            async with connect(url, additional_headers=headers, max_size=None, compression=None) as websocket:
                async def upload():
                    while chunk := await reader.read(65536):
                        await websocket.send(chunk)

                async def download():
                    async for chunk in websocket:
                        if not isinstance(chunk, bytes):
                            raise ValueError("ADB relay received a non-binary frame")
                        writer.write(chunk)
                        await writer.drain()

                upload_coroutine = upload()
                download_coroutine = download()
                upload_task = asyncio.create_task(upload_coroutine)
                download_task = asyncio.create_task(download_coroutine)
                tasks = [upload_task, download_task]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for running in tasks:
                        running.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as error:
            # Closing the TCP stream makes adb report a disconnected device.
            logger = logging.getLogger(__name__)
            logger.warning("ADB relay disconnected: %s", error)
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            self.tasks.discard(task)
