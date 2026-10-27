#!/usr/bin/env python3
"""Install the tailnet Simulator webcast as a per-user macOS LaunchAgent."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime

LABEL = 'com.codekunda.simulator-webcast'
ROOT = Path(__file__).resolve().parent
HOME = Path.home()
SUPPORT = HOME / 'Library/Application Support/SimulatorWebcast'
PLIST = HOME / 'Library/LaunchAgents' / (LABEL + '.plist')


def run(*args, check=True):
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=15)


def ask(label, value=None):
    prompt = f'{label} [{value}]: ' if value else f'{label}: '
    result = input(prompt).strip()
    return result or value


def devices():
    data = json.loads(run('xcrun', 'simctl', 'list', 'devices', 'available', '-j').stdout)
    return [(device['name'], device['udid'], device['state'])
            for runtime, items in data.get('devices', {}).items() if 'iOS' in runtime
            for device in items]


def tail_ip():
    candidates = [shutil.which('tailscale'), '/Applications/Tailscale.app/Contents/MacOS/Tailscale']
    for command in candidates:
        if command and Path(command).exists():
            result = run(command, 'ip', '-4', check=False)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().splitlines()[0]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udid')
    parser.add_argument('--bundle-id')
    parser.add_argument('--point-width', type=int)
    parser.add_argument('--point-height', type=int)
    parser.add_argument('--tailscale-ip')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--control-port', type=int, default=8766)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Run this installer on the Mac that hosts Simulator')
    if not (ROOT / 'server.py').is_file():
        parser.error('server.py must be beside install.py')
    found = devices()
    if not found:
        parser.error('No available iOS Simulator. Install one in Xcode → Settings → Platforms.')
    print('Available iOS Simulators:')
    for index, (name, udid, state) in enumerate(found, 1):
        print(f'  {index}. {name} ({state}) — {udid}')
    if not args.udid:
        selected = ask('Device number', '1')
        try:
            args.udid = found[int(selected) - 1][1]
        except (ValueError, IndexError):
            parser.error('Invalid device number')
    if args.udid not in {device[1] for device in found}:
        parser.error('UDID is not an available iOS Simulator')
    args.bundle_id = args.bundle_id or ask('Installed app bundle ID (e.g. com.example.MyApp)')
    args.point_width = args.point_width or int(ask('Screen width in logical points', '402'))
    args.point_height = args.point_height or int(ask('Screen height in logical points', '874'))
    args.tailscale_ip = args.tailscale_ip or tail_ip() or ask('Mac Tailscale IPv4')
    try:
        if ipaddress.ip_address(args.tailscale_ip) not in ipaddress.ip_network('100.64.0.0/10'):
            raise ValueError('IP is not a Tailscale IPv4 address')
    except ValueError as exc:
        parser.error(str(exc))
    if not args.bundle_id or args.point_width < 100 or args.point_height < 100:
        parser.error('Bundle ID and logical point dimensions are required')
    if not all(1024 <= port <= 65535 for port in (args.port, args.control_port)) or args.port == args.control_port:
        parser.error('Ports must be distinct and between 1024 and 65535')
    brew = shutil.which('brew') or '/opt/homebrew/bin/brew'
    if not Path(brew).exists():
        parser.error('Homebrew is required. Install it and idb-cli/idb-companion first.')
    prefix = run(brew, '--prefix', 'idb-cli').stdout.strip()
    python = Path(prefix) / 'libexec/bin/python3'
    bin_path = str(Path(brew).parent)
    env_path = f'{bin_path}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
    if not python.exists():
        parser.error('idb-cli Python not found; run brew install idb-cli idb-companion')
    if run(str(python), '-c', 'import idb.grpc.management', check=False).returncode:
        parser.error('idb Python import failed; reinstall idb-cli')
    if not shutil.which('idb', path=env_path) or not shutil.which('idb_companion', path=env_path):
        parser.error('idb and idb_companion are required on PATH')
    SUPPORT.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    if PLIST.exists():
        backup = PLIST.with_name(PLIST.name + '.bak-' + stamp)
        shutil.copy2(PLIST, backup)
        print(f'Backed up existing LaunchAgent: {backup}')
    target = SUPPORT / 'server.py'
    if target.exists():
        shutil.copy2(target, SUPPORT / ('server.py.bak-' + stamp))
    shutil.copy2(ROOT / 'server.py', target)
    environment = {
        'SIMULATOR_UDID': args.udid, 'APP_BUNDLE_ID': args.bundle_id,
        'SIM_POINT_WIDTH': str(args.point_width), 'SIM_POINT_HEIGHT': str(args.point_height),
        'TAILSCALE_IP': args.tailscale_ip, 'WEBCAST_PORT': str(args.port),
        'CONTROL_PORT': str(args.control_port), 'PATH': env_path,
    }
    plist = {
        'Label': LABEL, 'ProgramArguments': [str(python), str(target)],
        'EnvironmentVariables': environment, 'RunAtLoad': True, 'KeepAlive': True,
        'StandardOutPath': str(SUPPORT / 'server.log'),
        'StandardErrorPath': str(SUPPORT / 'server.err'),
    }
    with PLIST.open('wb') as file:
        plistlib.dump(plist, file)
    domain = f'gui/{os.getuid()}'
    run('launchctl', 'enable', f'{domain}/{LABEL}')
    run('launchctl', 'bootout', f'{domain}/{LABEL}', check=False)
    for attempt in range(3):
        result = run('launchctl', 'bootstrap', domain, str(PLIST), check=False)
        if result.returncode == 0:
            break
        time.sleep(0.75 * (attempt + 1))
    if result.returncode:
        parser.error(f'launchctl bootstrap failed: {result.stderr.strip()}. Retry: launchctl bootstrap {domain} {PLIST}')
    print(f'Installed. Open local control UI: http://127.0.0.1:{args.control_port}/')
    print(f'Tailnet preview (when enabled): http://{args.tailscale_ip}:{args.port}/')
    print('Broadcast starts OFF. Boot the selected Simulator, then click Start broadcast.')


if __name__ == '__main__':
    main()
