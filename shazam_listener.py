import asyncio
import sounddevice as sd
import numpy as np
import io
import wave
import requests
from shazamio import Shazam
from PIL import Image, ImageDraw
from io import BytesIO
import time
import threading
import json
import os

CONFIG_FILE = os.path.expanduser("~/config.json")

# Config defaults — used if not in config.json
DEFAULT_SHAZAM_CONFIG = {
    "silence_threshold": 50,
    "silence_timeout":   30,
    "sample_interval":   30,
    "sample_duration":   5,
    "retry_delay":       15,
}

def _get_cfg():
    """Read Shazam config values from config.json, falling back to defaults."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            return {k: cfg.get(k, v) for k, v in DEFAULT_SHAZAM_CONFIG.items()}
    except Exception:
        pass
    return DEFAULT_SHAZAM_CONFIG.copy()


# Audio config
SAMPLE_RATE = 44100
CHANNELS    = 1

# State
_current_track   = None
_is_sampling     = False
_listener_active = False
_loop            = None
_thread          = None
_stop_event      = None
_last_sound_time = None


def _find_input_device():
    """Find first available input device dynamically."""
    devices = sd.query_devices()
    if isinstance(devices, dict):
        if devices['max_input_channels'] > 0:
            return devices['index']
        return None
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            return i
    return None


def _rms(audio):
    return np.sqrt(np.mean(audio.astype(np.float32) ** 2))


def _record_sample():
    """Record audio sample. Returns None if silence."""
    global _last_sound_time
    cfg = _get_cfg()
    device = _find_input_device()
    if device is None:
        print("[Shazam] No input device found")
        return None
    print(f"[Shazam] Using device {device}")
    audio = sd.rec(int(cfg["sample_duration"] * SAMPLE_RATE),
                   samplerate=SAMPLE_RATE,
                   channels=CHANNELS,
                   dtype='int16',
                   device=device)
    sd.wait()
    rms = _rms(audio)
    print(f"[Shazam] RMS: {rms:.0f} threshold: {cfg['silence_threshold']}")
    if rms >= cfg["silence_threshold"]:
        _last_sound_time = time.time()
    if rms < cfg["silence_threshold"]:
        print("[Shazam] Silence detected, skipping")
        return None
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    buf.seek(0)
    return buf.read()


async def _recognize(wav_bytes):
    """Send to Shazam, return track dict or None."""
    try:
        shazam = Shazam()
        result = await shazam.recognize(wav_bytes)
        if 'track' in result:
            track  = result['track']
            title  = track.get('title', '')
            artist = track.get('subtitle', '')
            album  = ''
            for section in track.get('sections', []):
                for meta in section.get('metadata', []):
                    if meta.get('title', '').lower() == 'album':
                        album = meta.get('text', '')
                        break
            art_url = ''
            if 'images' in track and 'coverart' in track['images']:
                art_url = track['images']['coverart'].replace('400x400', '800x800')
            print(f"[Shazam] Matched: {artist} - {title} ({album})")
            return {
                'title':   title,
                'artist':  artist,
                'album':   album,
                'art_url': art_url,
            }
        else:
            print("[Shazam] No match")
            return None
    except Exception as e:
        print(f"[Shazam] Error: {e}")
        return None


def fetch_art(art_url):
    """Fetch album art from URL, return PIL Image or None."""
    try:
        r = requests.get(art_url, timeout=10)
        return Image.open(BytesIO(r.content))
    except Exception as e:
        print(f"[Shazam] Art fetch error: {e}")
        return None


def get_current_track():
    return _current_track


def is_sampling():
    return _is_sampling


def is_active():
    return _listener_active


def seconds_since_sound():
    """Return seconds since audio was last detected above threshold."""
    if _last_sound_time is None:
        return 999
    return time.time() - _last_sound_time


def draw_mic_icon(canvas, x, y, color=(200, 200, 200, 180), size=24):
    """Draw a simple microphone icon onto an RGBA canvas."""
    draw = ImageDraw.Draw(canvas)
    w  = int(size * 0.55)
    bh = int(size * 0.55)
    cx = x + size // 2

    body_x0 = cx - w // 2
    body_x1 = cx + w // 2
    body_y0 = y
    body_y1 = y + bh

    draw.ellipse([body_x0, body_y0, body_x1, body_y0 + w], fill=color)
    draw.rectangle([body_x0, body_y0 + w // 2, body_x1, body_y1], fill=color)
    draw.ellipse([body_x0, body_y1 - w, body_x1, body_y1], fill=color)

    arc_margin = int(size * 0.15)
    arc_x0 = x + arc_margin
    arc_x1 = x + size - arc_margin
    arc_y0 = body_y1 - int(size * 0.1)
    arc_y1 = y + size - int(size * 0.15)
    draw.arc([arc_x0, arc_y0, arc_x1, arc_y1], start=0, end=180, fill=color, width=2)

    stem_y0 = arc_y1
    stem_y1 = y + size - 2
    draw.line([cx, stem_y0, cx, stem_y1], fill=color, width=2)

    base_w = int(size * 0.4)
    draw.line([cx - base_w, stem_y1, cx + base_w, stem_y1], fill=color, width=2)

    return canvas


async def _listener_loop(stop_event):
    global _current_track, _is_sampling
    while not stop_event.is_set():
        cfg = _get_cfg()

        _is_sampling = True
        wav_bytes = await asyncio.get_event_loop().run_in_executor(None, _record_sample)

        if stop_event.is_set():
            _is_sampling = False
            break

        if wav_bytes is None:
            _is_sampling = False
            for _ in range(cfg["retry_delay"]):
                if stop_event.is_set():
                    break
                await asyncio.sleep(1)
            continue

        track = await _recognize(wav_bytes)
        _is_sampling = False

        if stop_event.is_set():
            break

        if track:
            _current_track = track
        else:
            _current_track = None

        # Wait sample_interval before next sample
        for _ in range(cfg["sample_interval"]):
            if stop_event.is_set():
                break
            await asyncio.sleep(1)


def _run_loop(stop_event):
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(_listener_loop(stop_event))
    _loop.close()


def start():
    global _listener_active, _thread, _stop_event, _current_track, _is_sampling, _last_sound_time
    if _listener_active:
        return
    _current_track   = None
    _is_sampling     = False
    _last_sound_time = None
    _stop_event      = threading.Event()
    _listener_active = True
    _thread = threading.Thread(target=_run_loop, args=(_stop_event,), daemon=True)
    _thread.start()
    print("[Shazam] Listener started")


def stop():
    global _listener_active, _current_track, _is_sampling, _last_sound_time
    if not _listener_active:
        return
    _stop_event.set()
    _listener_active = False
    _current_track   = None
    _is_sampling     = False
    _last_sound_time = None
    print("[Shazam] Listener stopped")
