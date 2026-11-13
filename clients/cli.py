import argparse
import asyncio
import json
import os
import signal
import sys
from contextlib import suppress

from clients.adb import adb
from clients.api import CloudClient, CloudError, configured_api_url
from clients.connections import connect_device, disconnect_device
from clients.credentials import CredentialError, load_credentials, remove_credentials
from clients.login import browser_login, revoke_credentials
from clients.relay import AdbRelay


def emit(value):
    rendered = json.dumps(value, indent=2)
    print(rendered)


async def run_command(client, args):
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise CloudError("Provide a command after --")
    lease = await client.provision(
        args.name, args.purpose,
        form_factor=args.form_factor, api_level=args.api_level, width=args.width, height=args.height, dpi=args.dpi,
    )
    lease_id = lease["id"]
    process = None
    try:
        await client.wait_active(lease, args.timeout)
        async with AdbRelay(client, lease_id, booking_name=args.name) as relay:
            connected = await adb("connect", relay.serial)
            print(connected, file=sys.stderr)
            try:
                state = await adb("-s", relay.serial, "get-state")
                if state != "device":
                    raise CloudError(f"ADB device is not ready: {state}")
                environment = os.environ.copy()
                environment.update(ANDROID_SERIAL=relay.serial, ADC_LEASE_ID=lease_id)
                process = await asyncio.create_subprocess_exec(*command, env=environment)
                return await process.wait()
            finally:
                with suppress(CloudError):
                    await adb("disconnect", relay.serial)
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                completion = process.wait()
                await asyncio.wait_for(completion, timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        await client.release(lease_id)


async def execute(args):
    if args.action == "login":
        api_url = configured_api_url()
        previous = load_credentials(api_url)
        if previous is None or args.switch:
            credentials = await browser_login(api_url)
            print(f"Connected to {credentials.account}.")
            if previous is not None and previous.token_id != credentials.token_id:
                try:
                    await asyncio.to_thread(revoke_credentials, previous)
                except CloudError:
                    print(f"adc: Revoke the previous CLI key for {previous.account} in the dashboard.", file=sys.stderr)
        else:
            print(f"Already connected to {previous.account}. Use adc login --switch to change accounts.")
        if os.environ.get("ADC_API_KEY"):
            print("ADC_API_KEY overrides this saved login. Unset it to use the browser login.")
        return 0
    if args.action == "logout":
        api_url = configured_api_url()
        credentials = load_credentials(api_url)
        if credentials is None:
            print("No saved login for this API.")
            return 0
        await asyncio.to_thread(revoke_credentials, credentials)
        remove_credentials(api_url)
        print("Saved CLI key revoked and local login removed.")
        return 0
    client = CloudClient.from_env()
    try:
        if args.action == "provision":
            lease = await client.provision(
                args.name, args.purpose, args.idempotency_key,
                form_factor=args.form_factor, api_level=args.api_level, width=args.width, height=args.height, dpi=args.dpi,
            )
            if args.wait:
                try:
                    lease = await client.wait_active(lease, args.timeout)
                except BaseException:
                    await client.release(lease["id"])
                    raise
            emit(lease)
        elif args.action == "release":
            lease = await client.release(args.lease_id)
            await disconnect_device(client, args.lease_id)
            emit(lease)
        elif args.action == "leases":
            leases = await client.request("GET", "/v2/leases")
            emit(leases)
        elif args.action == "device-options":
            options = await client.request("GET", "/v1/device-options")
            emit(options)
        elif args.action == "usage":
            usage = await client.request("GET", "/v1/usage")
            emit(usage)
        elif args.action == "connect":
            connection = await connect_device(client, args.lease_id, args.port)
            emit(connection)
        elif args.action == "disconnect":
            disconnected = await disconnect_device(client, args.lease_id)
            emit({"lease_id": args.lease_id, "disconnected": disconnected})
        elif args.action == "run":
            return await run_command(client, args)
        return 0
    finally:
        await client.close()


def parser():
    root = argparse.ArgumentParser(description="On-demand Android devices")
    sub = root.add_subparsers(dest="action", required=True)
    login = sub.add_parser("login", help="Sign in through your browser and save the CLI login")
    login.add_argument("--switch", action="store_true", help="Choose an account through your browser")
    sub.add_parser("logout", help="Revoke and remove the saved CLI login")
    provision = sub.add_parser("provision")
    provision.add_argument("--wait", action="store_true")
    provision.add_argument("--idempotency-key")
    run = sub.add_parser("run", help="Acquire, connect, run a command and release")
    for item in [provision, run]:
        item.add_argument("--name", default="Android device")
        item.add_argument("--purpose", choices=["development", "ci"], default="ci")
        item.add_argument("--timeout", type=int, default=600)
        item.add_argument("--form-factor", choices=["phone", "tv"], help="Device form factor from adc device-options")
        item.add_argument("--api-level", type=int, help="Android API level from adc device-options")
        item.add_argument("--width", type=int, help="Display width in pixels")
        item.add_argument("--height", type=int, help="Display height in pixels")
        item.add_argument("--dpi", type=int, help="Display density in DPI")
    run.add_argument("command", nargs=argparse.REMAINDER)
    for name in ["release", "connect", "disconnect"]:
        item = sub.add_parser(name)
        item.add_argument("lease_id")
        if name == "connect":
            item.add_argument("--port", type=int, default=0, help="Local ADB port; chooses an available port by default")
    sub.add_parser("leases")
    sub.add_parser("device-options", help="List device form factors, Android versions and display settings")
    sub.add_parser("usage")
    return root


def main():
    arguments = parser()
    args = arguments.parse_args()
    async def run_with_signals():
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        for signum in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(signum, task.cancel)
        return await execute(args)

    with asyncio.Runner() as runner:
        try:
            coroutine = run_with_signals()
            code = runner.run(coroutine)
        except (asyncio.CancelledError, KeyboardInterrupt):
            code = 130
        except (CloudError, CredentialError, OSError) as error:
            print(f"adc: {error}", file=sys.stderr)
            code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
