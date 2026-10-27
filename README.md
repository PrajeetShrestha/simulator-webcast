# Simulator Webcast

Private, interactive iOS Simulator preview over Tailscale. A local control page lets you choose an available iOS Simulator and start or stop the video broadcast. Video prefers `idb` MJPEG and falls back to `simctl` JPEG screenshots if the idb video surface is unavailable; browser taps and swipes use a persistent `idb` input connection.

## Requirements

**Mac, Xcode, and Simulator.** The Mac runs the iOS Simulator and the webcast server; the viewing phone does not need Xcode. Install the full Xcode app, open it once to finish setup, and add an iOS Simulator runtime in **Xcode → Settings → Platforms**. Verify with `xcodebuild -version` and `xcrun simctl list devices available`. If the latter lists no iOS devices, install the runtime before running the webcast installer. Xcode Command Line Tools alone are not enough.

**Logged-in desktop session.** Sign into the Mac's graphical desktop account and leave that account logged in while broadcasting. The per-user LaunchAgent runs in that account; it cannot log in at the lock screen after a reboot. Keep the Mac awake. Locking an already logged-in session is different from logging out, but sleep, logout, or shutdown interrupts the preview. After a reboot, log back in and boot the selected Simulator.

**Homebrew and Meta idb.** Homebrew installs `idb-cli` (the `idb` command and its Python package) and `idb-companion` (the Simulator-side companion). The installer checks both. idb provides the preferred MJPEG video stream and sends native taps/swipes; video can fall back to slower `simctl` screenshots, but interactive touch still needs idb. Install and verify them in **Prepare the Mac** below.

**Tailscale on both ends.** Install and connect Tailscale on the hosting Mac *and* each viewing phone/computer, using the same tailnet. The viewer opens the Mac's private Tailscale IPv4 on TCP 8765; the Mac-only control page is on localhost TCP 8766. No public internet exposure or port-forward is needed. Follow **Set up Tailscale** below and confirm your tailnet policy allows viewer → Mac TCP 8765.

**App ID and display size.** The installer asks for the initial Simulator's logical width and height in **points**, not a screenshot's pixel dimensions. For an iOS app, log `UIScreen.main.bounds.size` while that app runs on the chosen Simulator, or use that model's published logical-point dimensions. Find the app's bundle identifier in Xcode under **Target → General → Bundle Identifier**. A placeholder such as `com.example.MyApp` is fine if you only want to view Simulator Home; **Run app** works only when the configured bundle ID is installed on the selected Simulator. Switching devices updates dimensions automatically, but does not change the configured bundle ID.

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
   idb --version
   command -v idb_companion
   ```

3. Boot your chosen Simulator in Xcode/Simulator and build and run your app on it. Note the app's bundle ID and the device's logical display width/height in **points**, not screenshot pixels. The installer needs an initial bundle ID, but you can use a placeholder such as `com.example.MyApp` to preview Simulator Home; **Run app** only works once that bundle ID is installed.

## Install

On the **Mac hosting Simulator**, run this one-liner as the logged-in Mac user. It downloads the latest [release archive](https://github.com/PrajeetShrestha/simulator-webcast/releases/latest), unpacks it in a temporary directory, and starts the interactive installer (which asks for your Simulator, app bundle ID, display size, and Tailscale IP):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/PrajeetShrestha/simulator-webcast/main/install-latest.sh)"
```

The bootstrap requires `curl`, `tar`, `python3`, and GitHub access; it does not require `gh` or `jq`. It removes only its temporary download after installation. To inspect the script before running it, open [`install-latest.sh`](install-latest.sh). For a non-interactive agent install, append arguments after a placeholder `$0`:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/PrajeetShrestha/simulator-webcast/main/install-latest.sh)" _ --udid SIMULATOR_UDID --bundle-id com.example.MyApp --point-width WIDTH --point-height HEIGHT --tailscale-ip MAC_TAILSCALE_IP
```

The installer lists available iOS Simulators and asks for the device, app bundle ID, logical width/height, and Tailscale IPv4. It validates `idb` and creates a per-user LaunchAgent. For automation, use the non-interactive one-liner above; the same flags also work with `python3 install.py` if you downloaded the archive manually. Run it as the logged-in Mac user, **not with `sudo`**.

An existing `com.codekunda.simulator-webcast` LaunchAgent is backed up and replaced. The installed server is a per-user macOS login item and starts when that user logs in, but **broadcast defaults to off**. Existing server files are backed up before replacement.

## Use an existing AI coding agent

The agent does **not** connect to the video feed to build an app. Give it shell access to the **Mac hosting Xcode**; it creates/builds the native iOS project there, installs it on Simulator, and starts Simulator Webcast. You then watch and interact from a tailnet-connected browser. Any coding agent that can edit files and run shell commands works; no agent-specific plugin or API key is required.

1. **Give the agent Mac access.** If it already runs in the logged-in Mac account, use its local shell. If it runs on another computer/VPS, join that host to the same Tailscale tailnet, turn on **System Settings → General → Sharing → Remote Login** on the Mac, and authorize the agent host's SSH public key for the Mac user. From the agent host, verify `ssh MAC_USER@MAC_TAILSCALE_IP 'xcodebuild -version && xcrun simctl list devices available'`. Use the Mac's Tailscale IP and an SSH key; do not expose SSH or webcast ports publicly. Keep the Mac's GUI account logged in and awake—SSH alone cannot create that desktop session after a reboot.
2. **Give it the project requirements.** Specify the app behavior, a unique bundle ID (for example `com.example.MyApp`), the target Simulator, and the logical display size in points. Tell the agent to use Xcode/SwiftUI or your existing iOS project and to build for `iphonesimulator`, not a physical iPhone. The bundle ID it builds must match `--bundle-id` when installing Webcast. Simulator builds generally do not require device signing.
3. **Have it build and run on the Mac.** For an existing Xcode project, the agent can adapt this sequence (use `-workspace` instead of `-project` for a workspace; replace the scheme, path, and bundle ID):

   ```bash
   xcrun simctl list devices available
   xcrun simctl boot SIMULATOR_UDID                 # omit if already Booted
   xcrun simctl bootstatus SIMULATOR_UDID -b
   xcodebuild -project MyApp.xcodeproj -scheme MyApp \
     -sdk iphonesimulator -destination 'platform=iOS Simulator,id=SIMULATOR_UDID' \
     -derivedDataPath /tmp/myapp-simulator-build CODE_SIGNING_ALLOWED=NO build
   xcrun simctl install SIMULATOR_UDID \
     /tmp/myapp-simulator-build/Build/Products/Debug-iphonesimulator/MyApp.app
   xcrun simctl launch SIMULATOR_UDID com.example.MyApp
   ```

4. **Install Webcast in the same Mac user account** if it is not installed yet. The agent can use the non-interactive one-liner in **Install** above with its UDID, bundle ID, point dimensions, and Mac Tailscale IP. Replace `WIDTH`/`HEIGHT` with that Simulator's logical-point size. Do not use `sudo`. If Webcast is already installed with a different bundle ID or initial device, rerun the installer with the correct values; merely rebuilding the app does not update Webcast's configured ID.
5. **Start and verify the broadcast on the Mac.** Open `http://127.0.0.1:8766/` in the Mac's browser and switch **Broadcast** on. An agent with shell access to that Mac can also use the local-only control endpoint:

   ```bash
   curl -fsS -X POST -H 'Origin: http://127.0.0.1:8766' http://127.0.0.1:8766/enable
   curl -fsS http://127.0.0.1:8766/status
   curl -fsS http://MAC_TAILSCALE_IP:8765/health
   ```

   Confirm the selected Simulator is booted, `enabled` and `ready` are true, and the frame count rises. Open `http://MAC_TAILSCALE_IP:8765/` from the **viewing device** on the tailnet and test a tap. When finished, switch Broadcast off in the control page (or POST to `/disable` with the same Origin header). The agent's SSH connection is for build/control; the viewer connects to the separate preview port.

You can give the agent this brief: **“On my Tailscale-connected Mac, create a native SwiftUI app with bundle ID `com.example.MyApp` for the selected iOS Simulator. Build it for `iphonesimulator`, install and launch it with `simctl`, install/configure Simulator Webcast for that same UDID and bundle ID, enable broadcast locally, verify `/status` and `/health`, and report the private preview URL. Do not publish ports or use physical-device signing.”** Supply your actual app requirements and device details; the example values are placeholders.

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
