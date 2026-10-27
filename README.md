# Simulator Webcast

Private, interactive iOS Simulator preview over Tailscale. A local control page shows whether the selected Simulator is booted and lets you start or stop the video broadcast. Video prefers `idb` MJPEG and falls back to `simctl` JPEG screenshots if the idb video surface is unavailable; browser taps and swipes use a persistent `idb` input connection.

## Requirements

- macOS with Xcode and an installed iOS Simulator runtime
- A logged-in macOS desktop session
- Homebrew, Tailscale (signed in), and `brew install idb-cli idb-companion` after `brew tap facebook/fb`
- An app built and installed on the selected Simulator; its bundle ID and logical display size in **points**
- The viewing phone/computer connected to the same tailnet, permitted to reach the Mac on TCP 8765

## Install

Download the release archive from this repository, extract it on the **Mac hosting Simulator**, then run:

```bash
cd simulator-webcast
python3 install.py
```

The installer lists available iOS Simulators and asks for the device, installed app bundle ID, logical width/height, and Tailscale IPv4. It validates `idb` and creates a per-user LaunchAgent. For automation, pass `--udid`, `--bundle-id`, `--point-width`, `--point-height`, and `--tailscale-ip`; see `python3 install.py --help`.

An existing `com.codekunda.simulator-webcast` LaunchAgent is backed up and replaced. The installed server starts when that user logs in, but **broadcast defaults to off**. Existing server files are backed up before replacement.

## Use

1. Boot the selected Simulator in Xcode or Simulator and launch your app. The installer does not boot a Simulator or build your project.
2. On the Mac, open **http://127.0.0.1:8766/**. The page shows the selected Simulator UUID, the Mac’s Tailscale IP, and the exact preview URL. Check that Simulator says **Booted**, then click **Start broadcast**.
3. On a tailnet-connected viewing device, open the preview URL shown on the control page (`http://MAC_TAILSCALE_IP:8765/`). Tap or swipe the image to interact.
4. Click **Stop broadcast** in the local control page when done. The preview and input endpoints return unavailable while off, and the stream process is terminated. The enabled/disabled choice persists across service restarts.

Control UI is **localhost-only**. The preview binds only the Mac's configured Tailscale IPv4; it is not public. The control page always shows the address, but it only works from permitted tailnet devices when broadcast is on and the Mac/Tailscale connection is online. Anyone allowed to reach the preview port by your tailnet policy can view/control the Simulator while enabled. The HTTP Origin check is not authentication.

The Mac must remain awake, logged in, and connected to Tailscale. A LaunchAgent cannot log in or boot a shut-down Simulator after reboot. Physical iPhone features (camera, Bluetooth, push notifications, performance) are not simulated accurately.

## Checks and logs

```bash
curl http://127.0.0.1:8766/status
curl http://MAC_TAILSCALE_IP:8765/health
launchctl print "gui/$(id -u)/com.codekunda.simulator-webcast"
tail -n 50 "$HOME/Library/Application Support/SimulatorWebcast/server.err"
```

`/status` reports `simulator_booted`, `enabled`, `ready`, frame count, and frame age. `ready` requires a frame younger than three seconds. The fallback is slower than direct MJPEG; the log records when it is selected. If the `idb_companion` process dies, the server prunes its stale connection and retries the next tap or swipe. If video stops, check the exact Simulator's boot state, `idb_companion`, and Tailscale.

## Uninstall

```bash
launchctl bootout "gui/$(id -u)/com.codekunda.simulator-webcast"
rm "$HOME/Library/LaunchAgents/com.codekunda.simulator-webcast.plist"
```

The support directory is intentionally retained so logs, state, and backups are recoverable.
