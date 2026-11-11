# ADC

Use Cloud Devices from your terminal or GitHub Actions.

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

## GitHub Actions

Give the runner access to `adb` and store your Cloud Devices API key as the `ADC_API_KEY` repository secret. Add this step to your workflow.

```yaml
- uses: margelo/adc@v0.1.0
  with:
    api-key: ${{ secrets.ADC_API_KEY }}
    command: adb shell getprop sys.boot_completed
```

The action uses the same CLI and releases the device when the command exits. Use `api-url` to select a different API environment.

## Build the release archive

```sh
python3 scripts/build-release.py dist
```

Upload the generated archive to the matching GitHub release. Update the formula's version, URL, checksum, and Python resources before publishing a new release.
