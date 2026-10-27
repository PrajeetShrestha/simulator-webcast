#!/bin/bash
# Download the newest Simulator Webcast release and run its Mac installer.
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Run this on the Mac that hosts iOS Simulator.' >&2
  exit 1
fi
for command_name in curl python3 tar; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

release_api='https://api.github.com/repos/PrajeetShrestha/simulator-webcast/releases/latest'
archive_url="$(curl -fsSL "$release_api" | python3 -c 'import json,sys; release=json.load(sys.stdin); urls=[a["browser_download_url"] for a in release.get("assets", []) if a.get("name", "").endswith(".tar.gz")]; sys.exit("Latest release has no .tar.gz asset") if not urls else print(urls[0])')"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/simulator-webcast.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT
curl -fsSL "$archive_url" -o "$temp_dir/package.tar.gz"
tar -xzf "$temp_dir/package.tar.gz" -C "$temp_dir"
installer="$temp_dir/simulator-webcast/install.py"
if [[ ! -f "$installer" || ! -f "$temp_dir/simulator-webcast/server.py" ]]; then
  echo 'Release archive is missing install.py or server.py.' >&2
  exit 1
fi
cd "$temp_dir/simulator-webcast"
python3 install.py "$@"
