# Simulator Webcast

Private, interactive iOS Simulator preview over Tailscale. A local control page lets you choose an available iOS Simulator and start or stop the video broadcast. Video prefers `idb` MJPEG and falls back to `simctl` JPEG screenshots if the idb video surface is unavailable; browser taps and swipes use a persistent `idb` input connection.

## Requirements

- macOS with Xcode and an installed iOS Simulator runtime
- A logged-in macOS desktop session
- Homebrew and Meta `idb` (`idb-cli` and `idb-companion`)
- Tailscale on the Mac and viewing phone/computer, both connected to the same tailnet
- Your app's bundle ID and the initial device's logical display size in **points**; install the app on that Simulator to use **Run app**

## Set up Tailscale

1. Install [Tailscale for macOS](https://tailscale.com/download/mac) on the **Mac hosting Simulator**. Open it, sign in, connect, and grant any macOS VPN/network-extension permission requested. Signing up creates a tailnet if you do not already have one.
2. Install [Tailscale](https://tailscale.com/download) on the **viewing phone or computer**. Sign into the same tailnet and connect its VPN. On iPhone/iPad, accept the iOS VPN configuration prompt. No Tailscale Funnel/Serve, public DNS, VPS, or router port-forward is needed.
3. On the Mac, find its tailnet IPv4 address:

   ```bash
   tailscale ip -4
   ```

   If `tailscale` is not on `PATH`, try `/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4` or read the IP in the Tailscale app. The installer also attempts to detect it. Use this **Mac** address, not the viewer's address or a public IP.
4. Confirm both devices are online in the [Tailscale admin console](https://login.tailscale.com/admin/machines). The tailnet policy must permit the viewer to reach the Mac on **TCP 8765** (or your custom preview port). A new personal tailnet's default policy allows device-to-device connections; if you have custom grants/ACLs, add this connection there. The local control UI on TCP 8766 is not exposed to the tailnet.

The Mac must stay awake, logged in, and connected to Tailscale. The private IP is not a password: anyone your tailnet policy allows onto the preview port can view/control the Simulator while broadcasting.

## Prepare the Mac

1. Install [Xcode](https://developer.apple.com/xcode/) and launch it once. Install an iOS Simulator runtime in **Xcode → Settings → Platforms** if needed. `xcrun simctl list devices available` should list an iOS device.
2. Install [Homebrew](https://brew.sh/) if needed, then run:

   ```bash
   brew tap facebook/fb
   brew install idb-cli idb-companion
   ```

3. Boot your chosen Simulator in Xcode/Simulator and build and run your app on it. Note the app's bundle ID and the device's logical display width/height in **points**, not screenshot pixels. The installer needs an initial bundle ID, but you can use a placeholder such as `com.example.MyApp` to preview Simulator Home; **Run app** only works once that bundle ID is installed.

## Install

Download the [latest release archive](https://github.com/PrajeetShrestha/simulator-webcast/releases/latest) from this repository, extract it on the **Mac hosting Simulator**, then run from the directory containing `install.py` and `server.py`:

```bash
cd simulator-webcast
python3 install.py
```

The installer lists available iOS Simulators and asks for the device, app bundle ID, logical width/height, and Tailscale IPv4. It validates `idb` and creates a per-user LaunchAgent. For automation, pass `--udid`, `--bundle-id`, `--point-width`, `--point-height`, and `--tailscale-ip`; see `python3 install.py --help`. Run it as the logged-in Mac user, **not with `sudo`**.

An existing `com.codekunda.simulator-webcast` LaunchAgent is backed up and replaced. The installed server is a per-user macOS login item and starts when that user logs in, but **broadcast defaults to off**. Existing server files are backed up before replacement.

## Use

1. Boot the initially selected Simulator in Xcode or Simulator and launch your app. The installer does not boot a Simulator or build your project.
2. On the Mac, open **http://127.0.0.1:8766/**. The page lists available iOS Simulators with their runtime, boot status and UUID. Choosing a device updates the displayed and copied UUID immediately; click **Switch** to make that device active. The page shows boot/switch progress and the chosen device's actual state. A shut-down choice is booted and verified before the previously selected Simulator is shut down; unrelated booted Simulators are untouched. The Mac’s Tailscale IP and exact preview URL also have **Copy** buttons. Use the **Start control server at login** switch to control automatic startup, and the **Broadcast** switch to start or stop the preview.
3. Turn **Broadcast** on. On a tailnet-connected viewing device, open the preview URL shown on the control page (`http://MAC_TAILSCALE_IP:8765/`) in a browser. Tap or swipe the image to interact.
   **Home** presses the selected Simulator's Home button, regardless of which app is running; **Run app** launches the configured bundle ID.
4. Click **Stop broadcast** in the local control page when done. The preview and input endpoints return unavailable while off, and the stream process is terminated. Both the selected Simulator and enabled/disabled choice persist across service restarts. Switching while broadcasting retargets the stream and input without changing the broadcast setting. The configured app bundle ID stays the same, so **Run app** requires that app to be installed on the new Simulator.

Control UI is **localhost-only**. The preview binds only the Mac's configured Tailscale IPv4; it is not public. The control page always shows the address, but it only works from permitted tailnet devices when broadcast is on and the Mac/Tailscale connection is online. Anyone allowed to reach the preview port by your tailnet policy can view/control the Simulator while enabled. The HTTP Origin check is not authentication.

Removing the login item does not stop the current session; re-add it before logging out, or rerun the installer afterward. Reinstalling re-enables login startup. The Mac must remain awake, logged in, and connected to Tailscale. A LaunchAgent cannot log in or boot a shut-down Simulator after reboot. Physical iPhone features (camera, Bluetooth, push notifications, performance) are not simulated accurately.

## Checks and logs

```bash
curl http://127.0.0.1:8766/status
curl http://MAC_TAILSCALE_IP:8765/health
launchctl print "gui/$(id -u)/com.codekunda.simulator-webcast"
tail -n 50 "$HOME/Library/Application Support/SimulatorWebcast/server.err"
```

`/status` reports login-item state, available iOS Simulators, selected UUID, `simulator_booted`, switch target/phase and last switch result, `enabled`, `ready`, frame count, and frame age. `ready` requires a frame younger than three seconds. The fallback is slower than direct MJPEG; the log records when it is selected. If the `idb_companion` process dies, the server prunes its stale connection and retries the next tap or swipe. If video stops, check the exact Simulator's boot state, `idb_companion`, and Tailscale.

If the viewer cannot open the preview, first check that **Broadcast** is on, then check `/health` on the Mac. Confirm both devices are online in the same tailnet, `tailscale ip -4` still matches the installed Mac address, and tailnet policy permits viewer → Mac TCP 8765. If the Mac's Tailscale IP changed, rerun the installer with its new `--tailscale-ip`. Do not expose this port publicly as a workaround.

## Uninstall

```bash
launchctl bootout "gui/$(id -u)/com.codekunda.simulator-webcast"
rm "$HOME/Library/LaunchAgents/com.codekunda.simulator-webcast.plist"
```

The support directory is intentionally retained so logs, state, and backups are recoverable.
