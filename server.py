#!/usr/bin/env python3
import asyncio
import concurrent.futures
import http.server
import logging
import json
import os
import shutil
import ipaddress
import signal
import subprocess
import threading
import time
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
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
    enabled = bool(json.loads(STATE_PATH.read_text()).get('enabled', False))
except (OSError, ValueError, TypeError):
    enabled = False
active = threading.Event()
if enabled:
    active.set()

def simulator_booted():
    try:
        result = subprocess.run(['/usr/bin/xcrun', 'simctl', 'list', 'devices', 'booted', '-j'], capture_output=True, text=True, timeout=5, check=True)
        return any(device.get('udid') == DEVICE and device.get('state') == 'Booted'
                   for devices in json.loads(result.stdout).get('devices', {}).values() for device in devices)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False

def set_enabled(value):
    global enabled, frame, last_frame_time
    if value and not simulator_booted():
        raise RuntimeError('Selected Simulator is not booted')
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps({'enabled': value}) + '\n')
    temp.replace(STATE_PATH)
    with condition:
        enabled = value
        if value:
            active.set()
        else:
            active.clear()
            frame = b''
            last_frame_time = 0
            if stream_process is not None and stream_process.poll() is None:
                stream_process.terminate()
        condition.notify_all()

CONTROL_PAGE = '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Simulator Broadcast Control</title><style>body{font:16px -apple-system,BlinkMacSystemFont,sans-serif;background:#101827;color:#e5edf8;max-width:600px;margin:8vh auto;padding:20px}h1{font-size:27px}.card{background:#1d2b40;border:1px solid #35465e;border-radius:18px;padding:24px}.row{display:flex;justify-content:space-between;border-bottom:1px solid #35465e;padding:12px 0;gap:14px}.row:last-child{border:0}button{border:0;border-radius:10px;padding:13px 20px;font-weight:700;cursor:pointer;background:#53d8ab;color:#10251e}button.stop{background:#ff8990;color:#351315}button:disabled{opacity:.45;cursor:not-allowed}a{color:#8bc7ff;overflow-wrap:anywhere}#error{color:#ff9ca3;min-height:1.5em}small{color:#afbdd0}</style><h1>Simulator Broadcast</h1><p>Local controls on this Mac. The preview stays private to your tailnet.</p><div class="card"><div class="row"><span>Simulator</span><strong id="sim">Checking…</strong></div><div class="row"><span>Broadcast</span><strong id="broadcast">Checking…</strong></div><div class="row"><span>Video</span><strong id="video">Checking…</strong></div><div class="row"><span>Tailscale IP</span><strong id="tailip">Checking…</strong></div><div class="row"><span>Open from other tailnet devices</span><a id="preview" target="_blank" rel="noopener">Checking…</a></div><p><button id="toggle" disabled>Checking…</button></p><p id="error"></p><small>Boot the selected Simulator in Xcode or Simulator before enabling. Closing this page does not stop the broadcast.</small></div><p id="link"></p><script>let state=null;async function refresh(){try{const r=await fetch('/status',{cache:'no-store'});if(!r.ok)throw Error('Status unavailable');state=await r.json();document.getElementById('sim').textContent=state.simulator_booted?'Booted':'Off';document.getElementById('broadcast').textContent=state.enabled?'On':'Off';document.getElementById('video').textContent=state.ready?'Live':state.enabled?'Waiting for frames':'Off';document.getElementById('tailip').textContent=state.tailscale_ip;const link=document.getElementById('preview');link.href=state.preview_url;link.textContent=state.preview_url;const b=document.getElementById('toggle');b.textContent=state.enabled?'Stop broadcast':'Start broadcast';b.className=state.enabled?'stop':'';b.disabled=!state.enabled&&!state.simulator_booted;document.getElementById('link').textContent=state.enabled?'Accessible to permitted tailnet devices while the Simulator is booted.':'Preview is off. Start the broadcast to make this URL available to permitted tailnet devices.'}catch(e){document.getElementById('error').textContent=e.message}}document.getElementById('toggle').onclick=async()=>{const b=document.getElementById('toggle');b.disabled=true;document.getElementById('error').textContent='';try{const r=await fetch(state.enabled?'/disable':'/enable',{method:'POST'});if(!r.ok)throw Error(await r.text())}catch(e){document.getElementById('error').textContent=e.message}await refresh()};refresh();setInterval(refresh,3000)</script></html>'''

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
        command = [IDB, 'video-stream', '--format', 'mjpeg', '--fps', '10', '--scale-factor', '0.5', '--udid', DEVICE]
        frames_before = version
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            stream_process = process
            buffer = bytearray()
            while True:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk or not active.is_set():
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
                        if not active.is_set():
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
                fallback_capture()
                failures = 0
            else:
                time.sleep(1)

def fallback_capture():
    global frame, version, last_frame_time
    with tempfile.TemporaryDirectory(prefix='simulator-webcast-') as directory:
        image_path = Path(directory) / 'frame.jpg'
        while active.is_set():
            try:
                result = subprocess.run(['/usr/bin/xcrun','simctl','io',DEVICE,'screenshot','--type=jpeg',str(image_path)], capture_output=True, timeout=8)
                if result.returncode == 0:
                    data = image_path.read_bytes()
                    if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                        with condition:
                            if active.is_set():
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

async def dispatch_input(command, points):
    global input_context, input_client
    async with input_lock:
        if input_client is None:
            input_context = input_manager.from_udid(udid=DEVICE)
            input_client = await input_context.__aenter__()
        try:
            if command == 'tap':
                await input_client.tap(x=points[0], y=points[1])
            else:
                await input_client.swipe(p_start=(points[0], points[1]), p_end=(points[2], points[3]), duration=0.3)
        except Exception:
            try:
                await input_context.__aexit__(None, None, None)
            finally:
                input_context = None
                input_client = None
            raise

def send_input(command, points):
    future = asyncio.run_coroutine_threadsafe(dispatch_input(command, points), input_loop)
    return future.result(timeout=10)

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
                return self.reply(200,json.dumps({'enabled':enabled,'simulator_booted':simulator_booted(),'ready':enabled and bool(frame) and age is not None and age<3,'frames':version,'age_ms':round(age*1000) if age is not None else None,'tailscale_ip':HOST,'preview_url':'http://%s:%d/' % (HOST,PORT)}).encode(),'application/json')
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
            if self.path not in ('/enable','/disable'): return self.reply(404,b'Not found')
            try:
                set_enabled(self.path=='/enable')
                return self.reply(200,b'OK')
            except Exception as e: return self.reply(409,str(e).encode())
        if origin != 'http://%s:%d' % (HOST,PORT): return self.reply(403,b'Invalid origin')
        if not enabled: return self.reply(409,b'Broadcast is off')
        length=int(self.headers.get('Content-Length','0'))
        if length>1024: return self.reply(413,b'Too large')
        try:
            data=json.loads(self.rfile.read(length) or b'{}')
            if self.path=='/home':
                p=subprocess.run(['/usr/bin/xcrun','simctl','terminate',DEVICE,APP],capture_output=True,text=True,timeout=10)
                if p.returncode: raise RuntimeError(p.stderr.strip())
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
