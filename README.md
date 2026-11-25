# ADC

Use Cloud Devices from your terminal, GitHub Actions, or an MCP client.

## Homebrew

```sh
brew tap margelo/adc https://github.com/margelo/adc.git
brew install margelo/adc/adc
```

For device connections, install Android Platform Tools if `adb` is missing.

```sh
brew install --cask android-platform-tools
```

Run `adc login` for a Platform-connected workspace, or set `ADC_API_KEY` to a Cloud Devices API key. Use `adc run` to acquire a device, run a command with `ANDROID_SERIAL` set, and release the device when the command finishes.

```sh
adc run --name smoke -- adb shell getprop sys.boot_completed
```

Use `adc device-options` to list available form factors, Android API levels, display limits, and defaults. `adc provision` and `adc run` accept `--form-factor`, `--api-level`, `--width`, `--height`, and `--dpi`.

```sh
adc device-options
adc provision --form-factor tv --wait
adc run --name layout --api-level 35 --width 1080 --height 1920 --dpi 400 -- adb shell wm size
```

### Mirror a leased device

Install [scrcpy](https://github.com/Genymobile/scrcpy) and Android SDK Platform-Tools 36.0.0 or newer.

```sh
brew install scrcpy
adc mirror LEASE_ID
adc mirror LEASE_ID --full-resolution
```

The default mirror limits the video to 960 pixels on its longest side to reduce encoding load. Use `--full-resolution` for a sharper stream. Both commands use ADB Burst on a dedicated local ADB server.

If the lease already has a standard ADB connection, run `adc disconnect LEASE_ID` before opening the mirror. Closing the scrcpy window keeps the lease and local connection active. Use `adc disconnect LEASE_ID` to close the local connection and `adc release LEASE_ID` when you finish using the device.

Use `adc connect LEASE_ID --adb-burst` to opt in without opening a mirror. Run commands against the returned serial with `ADB_SERVER_SOCKET` set to `adb_server_socket` from the command's JSON output. For a disposable lease, `adc run --adb-burst -- COMMAND` sets the socket and `ANDROID_SERIAL` for the command and releases the lease when it exits.

## GitHub Actions

Give the runner access to `adb` and store your Cloud Devices API key as the `ADC_API_KEY` repository secret. Add this step to your workflow.

```yaml
- uses: margelo/adc@v0.5.1
  with:
    api-key: ${{ secrets.ADC_API_KEY }}
    api-level: '35'
    width: '1080'
    height: '1920'
    dpi: '400'
    command: adb shell getprop sys.boot_completed
```

The action uses the same CLI and releases the device when the command exits. Set `form-factor: tv` for Android TV. Each device setting is optional. Use `api-url` to select a different API environment.

## MCP

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and create a Cloud Devices API key in Platform. Add this configuration to an MCP client that supports local stdio servers.

```json
{
  "mcpServers": {
    "cloud-devices": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "agent-device-cloud-cli[mcp] @ git+https://github.com/margelo/adc.git@v0.5.1",
        "adc-mcp"
      ],
      "env": {
        "ADC_API_KEY": "<your-cloud-devices-api-key>"
      }
    }
  }
}
```

The server can provision, inspect, list, connect to, and release devices, and report usage. Install Android Platform Tools on the same machine to run the `adb` command returned by `connect_device`. Release the device when finished.

Use `list_device_options` to discover the available settings. Pass `form_factor: "tv"` to `provision_device` for Android TV. Pass `api_level`, `width`, `height`, or `dpi` to customize the device.

## Build the release archive

```sh
python3 scripts/build-release.py dist
```

Upload the generated archive to the matching GitHub release. Update the formula's version, URL, checksum, and Python resources before publishing a new release.
