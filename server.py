#!/usr/bin/env python3
import asyncio
import concurrent.futures
import http.server
import logging
import json
import os
import shutil
import ipaddress
import re
import signal
import subprocess
import threading
import time
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from idb.common.types import HIDButtonType
from idb.grpc.management import ClientManager

DEVICE = os.environ['SIMULATOR_UDID']
APP = os.environ['APP_BUNDLE_ID']
HOST = os.environ['TAILSCALE_IP']
PORT = int(os.environ.get('WEBCAST_PORT', '8765'))
CONTROL_PORT = int(os.environ.get('CONTROL_PORT', '8766'))
STATE_PATH = Path(os.environ.get('WEBCAST_STATE_PATH', str(Path.home() / '.config/simulator-webcast/state.json')))
POINT_WIDTH = int(os.environ['SIM_POINT_WIDTH'])
POINT_HEIGHT = int(os.environ['SIM_POINT_HEIGHT'])
IDB = shutil.which('idb')
COMPANION = shutil.which('idb_companion')
if not IDB or not COMPANION:
    raise SystemExit('idb and idb_companion must be on PATH')
if not ipaddress.ip_address(HOST) in ipaddress.ip_network('100.64.0.0/10'):
    raise SystemExit('TAILSCALE_IP must be a Tailscale IPv4 address')
if POINT_WIDTH < 100 or POINT_HEIGHT < 100:
    raise SystemExit('Set SIM_POINT_WIDTH and SIM_POINT_HEIGHT to logical screen points')
frame = b''
version = 0
last_frame_time = 0
stream_process = None
condition = threading.Condition()
try:
    saved_state = json.loads(STATE_PATH.read_text())
    if not isinstance(saved_state, dict):
        saved_state = {}
except (OSError, ValueError, TypeError):
    saved_state = {}
enabled = bool(saved_state.get('enabled', False))
if isinstance(saved_state.get('simulator_uuid'), str):
    DEVICE = saved_state['simulator_uuid']
POINT_WIDTH = int(saved_state.get('point_width', POINT_WIDTH))
POINT_HEIGHT = int(saved_state.get('point_height', POINT_HEIGHT))
active = threading.Event()
if enabled:
    active.set()

def simulators():
    try:
        result = subprocess.run(['/usr/bin/xcrun', 'simctl', 'list', 'devices', 'available', '-j'], capture_output=True, text=True, timeout=10, check=True)
        return [{'name': d['name'], 'udid': d['udid'], 'state': d['state'], 'runtime': runtime}
                for runtime, devices in json.loads(result.stdout).get('devices', {}).items() if 'iOS' in runtime
                for d in devices if d.get('isAvailable', True)]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []

def simulator_booted():
    return any(d['udid'] == DEVICE and d['state'] == 'Booted' for d in simulators())

def save_state():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps({'enabled': enabled, 'simulator_uuid': DEVICE,
                                'point_width': POINT_WIDTH, 'point_height': POINT_HEIGHT}) + '\n')
    temp.replace(STATE_PATH)

def select_simulator(udid):
    global DEVICE, POINT_WIDTH, POINT_HEIGHT, frame, last_frame_time
    if not isinstance(udid, str):
        raise ValueError('Invalid Simulator UUID')
    choices = {d['udid']: d for d in simulators()}
    if udid not in choices:
        raise ValueError('Simulator is not available')
    if udid == DEVICE:
        return
    if choices[udid]['state'] != 'Booted':
        result = subprocess.run(['/usr/bin/xcrun', 'simctl', 'boot', udid], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Could not boot Simulator')
        result = subprocess.run(['/usr/bin/xcrun', 'simctl', 'bootstatus', udid, '-b'], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Simulator boot failed')
    def display_value(key):
        result = subprocess.run(['/usr/bin/xcrun', 'simctl', 'getenv', udid, key], capture_output=True, text=True, timeout=10, check=True)
        return float(result.stdout.strip())
    try:
        scale = display_value('SIMULATOR_MAINSCREEN_SCALE')
        width = round(display_value('SIMULATOR_MAINSCREEN_WIDTH') / scale)
        height = round(display_value('SIMULATOR_MAINSCREEN_HEIGHT') / scale)
    except (OSError, subprocess.SubprocessError, ValueError, ZeroDivisionError) as exc:
        raise RuntimeError('Could not determine Simulator screen size') from exc
    with condition:
        DEVICE, POINT_WIDTH, POINT_HEIGHT = udid, width, height
        frame = b''
        last_frame_time = 0
        save_state()
        if stream_process is not None and stream_process.poll() is None:
            stream_process.terminate()
        condition.notify_all()

def set_enabled(value):
    global enabled, frame, last_frame_time
    if value and not simulator_booted():
        raise RuntimeError('Selected Simulator is not booted')
    with condition:
        enabled = value
        save_state()
        if value:
            active.set()
        else:
            active.clear()
            frame = b''
            last_frame_time = 0
            if stream_process is not None and stream_process.poll() is None:
                stream_process.terminate()
        condition.notify_all()

LOGIN_LABEL = 'com.codekunda.simulator-webcast'
LOGIN_TARGET = f'gui/{os.getuid()}/{LOGIN_LABEL}'

def login_item_enabled():
    result = subprocess.run(['/bin/launchctl', 'print-disabled', f'gui/{os.getuid()}'],
                            capture_output=True, text=True, timeout=5, check=True)
    match = re.search(r'"' + re.escape(LOGIN_LABEL) + r'"\s*=>\s*(enabled|disabled|true|false)', result.stdout)
    return match is None or match.group(1) in ('enabled', 'false')

def set_login_item(value):
    result = subprocess.run(['/bin/launchctl', 'enable' if value else 'disable', LOGIN_TARGET],
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Could not change login item')
    if login_item_enabled() != value:
        raise RuntimeError('Could not verify login item setting')

CONTROL_PAGE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Simulator Broadcast</title>
<style>
:root{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;color-scheme:light dark;--bg:#f5f5f7;--surface:#fff;--text:#1d1d1f;--secondary:#6e6e73;--line:#d9d9de;--accent:#007aff;--tint:#eaf3ff;--red:#d92d36;--shadow:0 1px 2px #0000000a,0 12px 36px #0000000b}
@media(prefers-color-scheme:dark){:root{--bg:#1c1c1e;--surface:#2c2c2e;--text:#f5f5f7;--secondary:#a1a1a6;--line:#48484a;--accent:#0a84ff;--tint:#143654;--red:#ff6961;--shadow:0 1px 2px #0004,0 12px 36px #0003}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-size:14px;line-height:1.4}main{max-width:760px;margin:0 auto;padding:48px 24px 64px}.heading{display:flex;align-items:center;gap:16px;margin-bottom:32px}.app-icon{width:54px;height:54px;flex:none;display:grid;place-items:center;border-radius:14px;background:linear-gradient(145deg,#62b1ff,#0868db);box-shadow:inset 0 1px #ffffff99,0 3px 9px #007aff45;color:white}.app-icon svg{width:32px;height:32px}h1{font-size:26px;letter-spacing:-.025em;line-height:1.1;margin:0;font-weight:700}.subtitle{margin:5px 0 0;color:var(--secondary)}.section{margin-top:28px}.section h2{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.065em;color:var(--secondary);margin:0 0 9px 12px}.group{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}.setting{min-height:58px;margin:0 16px;padding:12px 0;display:flex;align-items:center;justify-content:space-between;gap:16px}.setting+.setting{border-top:1px solid var(--line)}.setting.stack{align-items:stretch;flex-direction:column;gap:9px}.label{font-weight:500}.hint{display:block;color:var(--secondary);font-size:12px;font-weight:400;margin-top:3px}.value{min-width:0;text-align:right;color:var(--secondary)}.mono{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere;color:var(--text)}.value-actions{min-width:0;display:flex;align-items:center;justify-content:flex-end;gap:8px}.value-actions .mono{min-width:0;text-align:right}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:#98989d}.dot.on{background:#30b85d}.dot.wait{background:#ff9f0a}select,button{font:inherit}select{appearance:auto;max-width:100%;min-width:0;padding:7px 10px;border:1px solid var(--line);border-radius:7px;color:var(--text);background:var(--bg)}.device-select{width:100%}.picker{display:flex;gap:9px}.picker select{flex:1}.button{border-radius:7px;border:1px solid var(--line);background:var(--surface);color:var(--text);padding:7px 12px;white-space:nowrap;cursor:pointer;box-shadow:0 1px 2px #00000010}.button:hover:not(:disabled){background:var(--bg)}.button.primary{background:var(--accent);border-color:var(--accent);color:white}.button.primary:hover:not(:disabled){filter:brightness(1.08)}.button.small{font-size:12px;padding:5px 8px}.button:disabled,select:disabled{opacity:.5;cursor:default}.switch{position:relative;flex:none;display:inline-block;width:38px;height:23px;cursor:pointer}.switch input{position:absolute;inset:0;z-index:1;opacity:0;width:100%;height:100%;margin:0;cursor:inherit}.track{pointer-events:none;display:block;height:23px;border-radius:14px;background:#d0d0d5;border:1px solid #bcbcc2;transition:background .18s,border-color .18s}.track:after{content:"";display:block;width:19px;height:19px;margin:1px;border-radius:50%;background:white;box-shadow:0 1px 3px #0005;transition:transform .18s}.switch input:checked+.track{background:#30c75a;border-color:#30c75a}.switch input:checked+.track:after{transform:translateX(15px)}.switch input:disabled+.track{opacity:.5}.switch input:focus-visible+.track,.button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--accent);outline-offset:2px}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.footnote{font-size:12px;color:var(--secondary);margin:9px 12px 0}.message{min-height:20px;margin:22px 2px 0;color:var(--secondary);font-size:13px}.message.error{color:var(--red)}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:560px){main{padding:28px 16px 40px}.setting{margin:0 14px}.setting.wrap{align-items:flex-start;flex-direction:column;gap:7px}.setting.wrap .value-actions{width:100%;justify-content:space-between}.setting.wrap .mono{text-align:left}.picker{flex-wrap:wrap}.picker select{flex-basis:100%}.picker .button{margin-left:auto}.heading{margin-bottom:26px}h1{font-size:23px}}
@media(prefers-reduced-motion:reduce){.track,.track:after{transition:none}}
</style>
</head>
<body>
<main>
  <header class="heading">
    <div class="app-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><rect x="5" y="2.5" width="22" height="27" rx="5" stroke="currentColor" stroke-width="2"/><rect x="10" y="7" width="12" height="15" rx="2" fill="currentColor" opacity=".9"/><circle cx="16" cy="25.5" r="1.2" fill="currentColor"/></svg></div>
    <div><h1>Simulator Broadcast</h1><p class="subtitle">Control your iOS Simulator from this Mac.</p></div>
  </header>
  <section class="section" aria-labelledby="device-heading"><h2 id="device-heading">Simulator</h2><div class="group">
    <div class="setting stack"><label class="label" for="devices">Selected device</label><div class="picker"><select id="devices" class="device-select" aria-label="iOS Simulator"></select><button class="button primary" id="select" disabled>Switch</button></div></div>
    <div class="setting"><span class="label">State</span><span class="value" id="sim">Checking…</span></div>
    <div class="setting wrap"><span class="label">UUID</span><span class="value-actions"><span class="mono" id="uuid">Checking…</span><button class="button small" type="button" data-copy="uuid" aria-label="Copy Simulator UUID" disabled>Copy</button></span></div>
  </div><p class="footnote">Switching boots a shut-down Simulator. Your broadcast setting stays the same.</p></section>
  <section class="section" aria-labelledby="broadcast-heading"><h2 id="broadcast-heading">Broadcast</h2><div class="group">
    <div class="setting"><span class="label" id="broadcast-label">Broadcast<span class="hint">Allow permitted tailnet devices to view and control the Simulator.</span></span><label class="switch"><input id="broadcast-toggle" type="checkbox" role="switch" aria-labelledby="broadcast-label" disabled><span class="track" aria-hidden="true"></span></label></div>
    <div class="setting"><span class="label">Video</span><span class="value" id="video">Checking…</span></div>
  </div></section>
  <section class="section" aria-labelledby="network-heading"><h2 id="network-heading">Tailnet access</h2><div class="group">
    <div class="setting wrap"><span class="label">Tailscale IP</span><span class="value-actions"><span class="mono" id="tailip">Checking…</span><button class="button small" type="button" data-copy="tailip" aria-label="Copy Tailscale IP" disabled>Copy</button></span></div>
    <div class="setting wrap"><span class="label">Preview address</span><span class="value-actions"><a class="mono" id="preview" target="_blank" rel="noopener">Checking…</a><button class="button small" type="button" data-copy="preview" aria-label="Copy preview address" disabled>Copy</button></span></div>
  </div><p class="footnote" id="link">Checking preview availability…</p></section>
  <section class="section" aria-labelledby="general-heading"><h2 id="general-heading">General</h2><div class="group">
    <div class="setting"><span class="label" id="login-label">Start control server at login<span class="hint">The broadcast itself remains off until you enable it.</span></span><label class="switch"><input id="login-toggle" type="checkbox" role="switch" aria-labelledby="login-label" disabled><span class="track" aria-hidden="true"></span></label></div>
  </div><p class="footnote">Disabling login startup does not stop this session. The Mac must be awake, logged in, and connected to Tailscale.</p></section>
  <p id="message" class="message" role="status" aria-live="polite"></p>
</main>
<script>
let state=null,busy=false,refreshing=false,pendingDevice=null;
const $=id=>document.getElementById(id);
function message(text,error=false){const el=$('message');el.textContent=text;el.classList.toggle('error',error)}
function setBusy(value){busy=value;for(const id of ['devices','select','broadcast-toggle','login-toggle'])$(id).disabled=value;if(!value&&state)render()}
function statusDot(text,kind){const el=document.createElement('span');el.className='dot '+kind;el.setAttribute('aria-hidden','true');const label=document.createElement('span');label.textContent=text;return [el,label]}
function render(){if(!state)return;
  $('sim').replaceChildren(...statusDot(state.simulator_booted?'Booted':'Shut down',state.simulator_booted?'on':''));
  $('tailip').textContent=state.tailscale_ip;
  const preview=$('preview');preview.href=state.preview_url;preview.textContent=state.preview_url;
  $('video').replaceChildren(...statusDot(state.ready?'Live':state.enabled?'Waiting for frames':'Off',state.ready?'on':state.enabled?'wait':''));
  $('broadcast-toggle').checked=state.enabled;$('login-toggle').checked=state.login_item;
  const picker=$('devices'),wanted=pendingDevice||state.simulator_uuid;picker.replaceChildren();
  for(const d of state.simulators){const option=document.createElement('option');option.value=d.udid;option.textContent=d.name+' · '+d.runtime.replace('com.apple.CoreSimulator.SimRuntime.','').replaceAll('-',' ')+' · '+d.state+' · '+d.udid.slice(0,8);picker.append(option)}
  picker.value=state.simulators.some(d=>d.udid===wanted)?wanted:state.simulator_uuid;
  $('uuid').textContent=picker.value||state.simulator_uuid;
  picker.disabled=busy;$('select').disabled=busy||!picker.value||picker.value===state.simulator_uuid;
  $('broadcast-toggle').disabled=busy||(!state.enabled&&!state.simulator_booted);
  $('login-toggle').disabled=busy;
  for(const button of document.querySelectorAll('[data-copy]'))button.disabled=!$(button.dataset.copy).textContent||$(button.dataset.copy).textContent==='Checking…';
  $('link').textContent=state.enabled?'Available to permitted tailnet devices while the Simulator is booted.':'Preview is off. Turn on Broadcast to make this address available.';
}
async function refresh(){if(refreshing||busy)return;refreshing=true;try{const response=await fetch('/status',{cache:'no-store'});if(!response.ok)throw Error('Status unavailable');state=await response.json();render()}catch(error){message(error.message,true)}finally{refreshing=false}}
async function post(path,data){const options={method:'POST'};if(data){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(data)}const response=await fetch(path,options);if(!response.ok)throw Error(await response.text())}
$('devices').addEventListener('change',()=>{pendingDevice=$('devices').value;$('uuid').textContent=pendingDevice;$('select').disabled=busy||!state||pendingDevice===state.simulator_uuid});
$('select').addEventListener('click',async()=>{const udid=$('devices').value;setBusy(true);message('Switching Simulator…');try{await post('/select',{udid});pendingDevice=null;message('Simulator switched.')}catch(error){message(error.message,true)}finally{busy=false;await refresh();render()}});
$('broadcast-toggle').addEventListener('change',async event=>{const desired=event.target.checked;setBusy(true);message('');try{await post(desired?'/enable':'/disable');message(desired?'Broadcast started.':'Broadcast stopped.')}catch(error){event.target.checked=!desired;message(error.message,true)}finally{setBusy(false);await refresh()}});
$('login-toggle').addEventListener('change',async event=>{const desired=event.target.checked;setBusy(true);message('');try{await post(desired?'/login-enable':'/login-disable');message(desired?'Starts at login.':'Login startup disabled.')}catch(error){event.target.checked=!desired;message(error.message,true)}finally{setBusy(false);await refresh()}});
async function copy(text){if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);return}const input=document.createElement('textarea');input.value=text;input.style.position='fixed';input.style.opacity='0';document.body.append(input);input.select();const okay=document.execCommand('copy');input.remove();if(!okay)throw Error('Clipboard unavailable in this browser')}
for(const button of document.querySelectorAll('[data-copy]'))button.addEventListener('click',async()=>{const target=$(button.dataset.copy);const value=button.dataset.copy==='preview'?target.href:target.textContent;try{await copy(value);button.textContent='Copied';message('Copied to clipboard.');setTimeout(()=>button.textContent='Copy',1600)}catch(error){message(error.message,true)}});
refresh();setInterval(refresh,3000);
</script>
</body>
</html>
'''
PAGE = '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#111827"><title>iOS Simulator Preview</title><style>body{margin:0;background:#111827;color:#e5e7eb;font:15px -apple-system,BlinkMacSystemFont,sans-serif;text-align:center}header{padding:13px;font-weight:600}.tag{color:#93c5fd;font-size:12px;margin-left:8px}.wrap{display:flex;align-items:center;justify-content:center;height:calc(100dvh - 110px)}#screen{height:100%;max-width:100%;object-fit:contain;border-radius:22px;touch-action:none;user-select:none;-webkit-user-select:none}footer{min-height:42px;display:flex;justify-content:center;align-items:center;gap:12px;flex-wrap:wrap}body.immersive header{display:none}body.immersive .wrap{height:100dvh}body.immersive #screen{border-radius:0}body.immersive footer{position:fixed;bottom:calc(8px + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);background:#111827cc;border-radius:12px;padding:4px}body.immersive footer #home,body.immersive footer #run,body.immersive footer a,body.immersive footer #status{display:none}button{background:#374151;border:0;border-radius:9px;color:white;padding:9px 16px}#status{color:#9ca3af;font-size:12px}</style><header>iOS Simulator <span class="tag">Tailnet only</span></header><div class="wrap"><img id="screen" src="/stream" alt="Simulator screen"></div><footer><button id="home">Home</button><button id="run">Run app</button><button id="fullscreen">Full screen</button><a href="/frame.jpg" style="color:#93c5fd" target="_blank">Screenshot</a><span id="status">Live preview</span></footer><script>const image=document.getElementById('screen'),status=document.getElementById('status');function send(path,data){fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})}).then(async r=>{if(!r.ok)throw Error(await r.text());status.textContent='Sent';setTimeout(()=>status.textContent='Live preview',1500)}).catch(e=>status.textContent=e.message)}document.getElementById('home').onclick=()=>send('/home');document.getElementById('run').onclick=()=>send('/run');const full=document.getElementById('fullscreen');full.onclick=async()=>{const entering=!document.body.classList.contains('immersive');document.body.classList.toggle('immersive',entering);full.textContent=entering?'Exit full screen':'Full screen';if(entering&&document.documentElement.requestFullscreen){try{await document.documentElement.requestFullscreen()}catch(e){/* iPhone Safari may not allow element fullscreen */}}else if(!entering&&document.fullscreenElement&&document.exitFullscreen){await document.exitFullscreen()}};document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement&&document.body.classList.contains('immersive')&&document.documentElement.requestFullscreen){document.body.classList.remove('immersive');full.textContent='Full screen'}});image.onerror=()=>status.textContent='Stream disconnected';
function point(ev){const r=image.getBoundingClientRect(),w=image.naturalWidth,h=image.naturalHeight;if(!w||!h)return null;const scale=Math.min(r.width/w,r.height/h),dw=w*scale,dh=h*scale,left=r.left+(r.width-dw)/2,top=r.top+(r.height-dh)/2;const x=(ev.clientX-left)/dw,y=(ev.clientY-top)/dh;return x>=0&&x<=1&&y>=0&&y<=1?{x,y}:null}
let gesture=null;image.addEventListener('pointerdown',ev=>{const p=point(ev);if(!p)return;gesture={p,time:performance.now(),id:ev.pointerId};try{image.setPointerCapture(ev.pointerId)}catch(e){}ev.preventDefault()});image.addEventListener('pointerup',ev=>{if(!gesture||gesture.id!==ev.pointerId)return;const end=point(ev),start=gesture;gesture=null;if(!end)return;const distance=Math.hypot(end.x-start.p.x,end.y-start.p.y);send(distance>0.025?'/swipe':'/tap',distance>0.025?{x1:start.p.x,y1:start.p.y,x2:end.x,y2:end.y}:{x:end.x,y:end.y});ev.preventDefault()});image.addEventListener('pointercancel',()=>gesture=null);</script></html>'''

def capture():
    global frame, version, last_frame_time, stream_process
    failures = 0
    while True:
        active.wait()
        env = os.environ.copy()
        env['PATH'] = os.path.dirname(IDB) + ':' + os.path.dirname(COMPANION) + ':/usr/bin:/bin'
        capture_device = DEVICE
        command = [IDB, 'video-stream', '--format', 'mjpeg', '--fps', '10', '--scale-factor', '0.5', '--udid', capture_device]
        frames_before = version
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            stream_process = process
            buffer = bytearray()
            while True:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk or not active.is_set() or capture_device != DEVICE:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b'\xff\xd8')
                    if start < 0:
                        buffer[:] = buffer[-1:]
                        break
                    end = buffer.find(b'\xff\xd9', start + 2)
                    if end < 0:
                        if start:
                            del buffer[:start]
                        if len(buffer) > 2_000_000:
                            buffer.clear()
                        break
                    data = bytes(buffer[start:end + 2])
                    del buffer[:end + 2]
                    with condition:
                        if not active.is_set() or capture_device != DEVICE:
                            break
                        frame = data
                        version += 1
                        last_frame_time = time.monotonic()
                        condition.notify_all()
            print('idb video stream exited: %s' % process.poll(), flush=True)
        except Exception as exc:
            print('idb video stream failed: %s' % exc, flush=True)
        finally:
            if stream_process is not None and stream_process.poll() is None:
                stream_process.terminate()
            stream_process = None
        if active.is_set():
            failures = 0 if version > frames_before else failures + 1
            if failures >= 3:
                print('idb video unavailable; using simctl JPEG fallback', flush=True)
                fallback_capture(capture_device)
                failures = 0
            else:
                time.sleep(1)

def fallback_capture(capture_device):
    global frame, version, last_frame_time
    with tempfile.TemporaryDirectory(prefix='simulator-webcast-') as directory:
        image_path = Path(directory) / 'frame.jpg'
        while active.is_set() and capture_device == DEVICE:
            try:
                result = subprocess.run(['/usr/bin/xcrun','simctl','io',capture_device,'screenshot','--type=jpeg',str(image_path)], capture_output=True, timeout=8)
                if result.returncode == 0:
                    data = image_path.read_bytes()
                    if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                        with condition:
                            if active.is_set() and capture_device == DEVICE:
                                frame = data
                                version += 1
                                last_frame_time = time.monotonic()
                                condition.notify_all()
            except (OSError, subprocess.SubprocessError) as exc:
                print('Screenshot fallback failed: %s' % exc, flush=True)
            time.sleep(0.5)

def stop_stream(signum, ignored):
    if stream_process is not None and stream_process.poll() is None:
        stream_process.terminate()
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop_stream)
signal.signal(signal.SIGINT, stop_stream)

input_loop = asyncio.new_event_loop()
input_lock = asyncio.Lock()
input_manager = ClientManager(logger=logging.getLogger('simulator-webcast'), companion_path=COMPANION)
input_context = None
input_client = None
input_device = None

async def dispatch_input(command, points):
    global input_context, input_client, input_device
    async with input_lock:
        if input_client is not None and input_device != DEVICE:
            await input_context.__aexit__(None, None, None)
            input_context = None
            input_client = None
        if input_client is None:
            input_context = input_manager.from_udid(udid=DEVICE)
            input_client = await input_context.__aenter__()
            input_device = DEVICE
        try:
            if command == 'tap':
                await input_client.tap(x=points[0], y=points[1])
            elif command == 'swipe':
                await input_client.swipe(p_start=(points[0], points[1]), p_end=(points[2], points[3]), duration=0.3)
            elif command == 'home':
                await input_client.button(button_type=HIDButtonType.HOME)
        except Exception:
            try:
                await input_context.__aexit__(None, None, None)
            finally:
                input_context = None
                input_client = None
                input_device = None
            raise

def send_input(command, points):
    def run(coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, input_loop).result(timeout=10)
    try:
        return run(dispatch_input(command, points))
    except Exception as exc:
        # idb persists companion socket metadata after the companion exits. Prune the
        # dead entry and spawn a fresh companion on the next attempt.
        message = str(exc).lower()
        if not any(phrase in message for phrase in ('connection refused', 'failed to connect to companion', 'statuscode.unavailable')):
            raise
        run(input_manager.list_targets())
        return run(dispatch_input(command, points))

threading.Thread(target=lambda: (asyncio.set_event_loop(input_loop), input_loop.run_forever()), daemon=True).start()

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self, fmt, *args):
        print('%s %s' % (self.address_string(), fmt % args), flush=True)
    def reply(self, code, content, typ='text/plain'):
        self.send_response(code)
        self.send_header('Content-Type',typ)
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)
    def do_GET(self):
        path=urlsplit(self.path).path
        if self.server.is_control:
            if path=='/': return self.reply(200,CONTROL_PAGE.encode(),'text/html; charset=utf-8')
            if path=='/status':
                age = time.monotonic()-last_frame_time if last_frame_time else None
                devices = simulators()
                return self.reply(200,json.dumps({'enabled':enabled,'simulator_booted':any(d['udid']==DEVICE and d['state']=='Booted' for d in devices),'simulator_uuid':DEVICE,'simulators':devices,'ready':enabled and bool(frame) and age is not None and age<3,'frames':version,'age_ms':round(age*1000) if age is not None else None,'tailscale_ip':HOST,'preview_url':'http://%s:%d/' % (HOST,PORT),'login_item':login_item_enabled()}).encode(),'application/json')
            return self.reply(404,b'Not found')
        if path=='/' and not enabled: return self.reply(503,b'Broadcast is off. Open the local control page on the Mac.', 'text/plain')
        if path=='/': return self.reply(200,PAGE.encode(),'text/html; charset=utf-8')
        if path=='/health': return self.reply(200,json.dumps({'frames':version,'enabled':enabled,'simulator_booted':simulator_booted(),'ready':enabled and bool(frame) and time.monotonic()-last_frame_time<3,'age_ms':round((time.monotonic()-last_frame_time)*1000) if last_frame_time else None}).encode(),'application/json')
        if not enabled and path in ('/frame.jpg','/stream'): return self.reply(503,b'Broadcast is off')
        if path=='/frame.jpg': return self.reply(200,frame,'image/jpeg') if frame else self.reply(503,b'No frame yet')
        if path!='/stream': return self.reply(404,b'Not found')
        self.send_response(200)
        self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        last=0
        try:
            while True:
                with condition:
                    condition.wait_for(lambda:version>last or not enabled,timeout=15)
                    if not enabled: break
                    if version<=last: continue
                    data=frame
                    last=version
                self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data+b'\r\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError): pass
    def do_POST(self):
        origin=self.headers.get('Origin','')
        if self.server.is_control:
            if self.headers.get('Host') not in ('127.0.0.1:%d' % CONTROL_PORT, 'localhost:%d' % CONTROL_PORT): return self.reply(403,b'Invalid host')
            if origin not in ('http://127.0.0.1:%d' % CONTROL_PORT, 'http://localhost:%d' % CONTROL_PORT): return self.reply(403,b'Invalid origin')
            if self.path not in ('/enable','/disable','/select','/login-enable','/login-disable'): return self.reply(404,b'Not found')
            try:
                if self.path=='/select':
                    length=int(self.headers.get('Content-Length','0'))
                    if length<1 or length>1024: return self.reply(413,b'Invalid request size')
                    data=json.loads(self.rfile.read(length))
                    select_simulator(data.get('udid'))
                elif self.path.startswith('/login-'):
                    set_login_item(self.path=='/login-enable')
                else:
                    set_enabled(self.path=='/enable')
                return self.reply(200,b'OK')
            except (ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as e: return self.reply(409,str(e).encode())
        if origin != 'http://%s:%d' % (HOST,PORT): return self.reply(403,b'Invalid origin')
        if not enabled: return self.reply(409,b'Broadcast is off')
        length=int(self.headers.get('Content-Length','0'))
        if length>1024: return self.reply(413,b'Too large')
        try:
            data=json.loads(self.rfile.read(length) or b'{}')
            if self.path=='/home':
                send_input('home',[])
            elif self.path=='/run':
                p=subprocess.run(['/usr/bin/xcrun','simctl','launch',DEVICE,APP],capture_output=True,text=True,timeout=10)
                if p.returncode: raise RuntimeError(p.stderr.strip())
            elif self.path in ('/tap','/swipe'):
                def coord(key, extent):
                    value=data.get(key)
                    if not isinstance(value,(int,float)) or isinstance(value,bool) or not 0 <= value <= 1:
                        raise ValueError('Invalid coordinate')
                    return str(round(value * (extent-1)))
                if self.path=='/tap':
                    points=[int(coord('x',POINT_WIDTH)),int(coord('y',POINT_HEIGHT))]
                    send_input('tap',points)
                else:
                    points=[int(coord('x1',POINT_WIDTH)),int(coord('y1',POINT_HEIGHT)),int(coord('x2',POINT_WIDTH)),int(coord('y2',POINT_HEIGHT))]
                    send_input('swipe',points)
            else: return self.reply(404,b'Not found')
            return self.reply(200,b'OK')
        except Exception as e: return self.reply(500,str(e).encode())

threading.Thread(target=capture,daemon=True).start()
control = http.server.ThreadingHTTPServer(('127.0.0.1',CONTROL_PORT),Handler)
control.is_control = True
threading.Thread(target=control.serve_forever,daemon=True).start()
broadcast = http.server.ThreadingHTTPServer((HOST,PORT),Handler)
broadcast.is_control = False
print('Control: http://127.0.0.1:%d/ | Preview: http://%s:%d/' % (CONTROL_PORT,HOST,PORT), flush=True)
broadcast.serve_forever()
