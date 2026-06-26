import requests
import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont
from io import BytesIO
import time
import math
from datetime import datetime
import pigpio
import json
import os
import shazam_listener

CONFIG_FILE = os.path.expanduser("~/config.json")

DEFAULT_CONFIG = {
    "bridge": "http://192.168.8.118:3001",
    "target_zone": "Lounge",
    "poll_interval": 10,
    "text_hold": 20,
    "scroll_speed": 1.5,
    "scroll_hold": 2.0,
    "scroll_fps": 20,
    "size_artist": 32,
    "size_album": 26,
    "size_track": 26,
    "line_spacing": 6,
    "text_bg_opacity": 180,
    "text_bg_blur": 12,
    "progress_bar_height": 3,
    "progress_bar_colour": "#ffffff",
    "blur_radius": 30,
    "clock_blur_radius": 40,
    "full_brightness": 255,
    "dim_brightness": 51,
    "night_brightness": 20,
    "dim_after_secs": 120,
    "daytime_start": 8,
    "daytime_end": 20,
    "night_start": 23,
    "night_end": 6,
    "pwm_freq": 2000,
    "clock_radius": 200,
    "clock_cx": 570,
    "clock_date_cx": 175,
    "clock_flip": False,
    "silence_threshold": 50,
    "silence_timeout": 30,
    "sample_interval": 30,
    "sample_duration": 5,
    "retry_delay": 15,
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()

cfg = load_config()
config_mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0

def maybe_reload_config():
    global cfg, config_mtime
    if os.path.exists(CONFIG_FILE):
        mtime = os.path.getmtime(CONFIG_FILE)
        if mtime != config_mtime:
            cfg = load_config()
            config_mtime = mtime
            print("Config reloaded")

FB = "/dev/fb0"
FONT_BOLD = "/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-bold.otf"
FONT_REG  = "/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-regular.otf"
PAD        = 10
MARGIN     = 12
MAX_TEXT_W = 480

pi = pigpio.pi()

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def set_brightness(level):
    pi.set_PWM_frequency(19, cfg["pwm_freq"])
    pi.set_PWM_dutycycle(19, max(0, min(255, level)))

def get_brightness_for_time():
    h = datetime.now().hour
    ns = cfg["night_start"]
    ne = cfg["night_end"]
    ds = cfg["daytime_start"]
    de = cfg["daytime_end"]
    if ns > ne:
        is_night = h >= ns or h < ne
    else:
        is_night = ns <= h < ne
    if is_night:
        return cfg["night_brightness"]
    if ds <= h < de:
        return cfg["full_brightness"]
    return cfg["dim_brightness"]

def rgb888_to_rgb565(img):
    img = img.convert('RGB')
    r = np.array(img)[:, :, 0].astype(np.uint16)
    g = np.array(img)[:, :, 1].astype(np.uint16)
    b = np.array(img)[:, :, 2].astype(np.uint16)
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb565.astype(np.uint16).tobytes()

def write_fb(img):
    img = img.rotate(90, expand=True)
    with open(FB, 'wb') as f:
        f.write(rgb888_to_rgb565(img))


# --- Clock ---

def draw_clock_on_canvas(canvas, show_mic=False):
    now  = datetime.now()
    draw = ImageDraw.Draw(canvas)
    r    = cfg["clock_radius"]
    flip = cfg.get("clock_flip", False)
    if flip:
        cx      = 800 - cfg["clock_cx"]
        date_cx = 800 - cfg["clock_date_cx"]
    else:
        cx      = cfg["clock_cx"]
        date_cx = cfg["clock_date_cx"]
    cy = 240

    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=2)
    for i in range(12):
        angle = math.radians(i * 30 - 90)
        if i % 3 == 0:
            inner, outer, w = r - 20, r - 4, 3
        else:
            inner, outer, w = r - 12, r - 4, 2
        x1 = cx + inner * math.cos(angle)
        y1 = cy + inner * math.sin(angle)
        x2 = cx + outer * math.cos(angle)
        y2 = cy + outer * math.sin(angle)
        draw.line([x1, y1, x2, y2], fill=(255, 255, 255), width=w)

    hour_angle = math.radians((now.hour % 12) * 30 + now.minute * 0.5 - 90)
    hx = cx + (r * 0.55) * math.cos(hour_angle)
    hy = cy + (r * 0.55) * math.sin(hour_angle)
    draw.line([cx, cy, hx, hy], fill=(255, 255, 255), width=6)

    min_angle = math.radians(now.minute * 6 - 90)
    mx = cx + (r * 0.8) * math.cos(min_angle)
    my = cy + (r * 0.8) * math.sin(min_angle)
    draw.line([cx, cy, mx, my], fill=(255, 255, 255), width=4)
    draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(255, 255, 255))

    day_str  = now.strftime("%A")
    date_str = now.strftime("%-d %B %y")
    font_day  = ImageFont.truetype(FONT_BOLD, 44)
    font_date = ImageFont.truetype(FONT_REG,  42)

    def text_w(text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    dw, dh   = text_w(day_str,  font_day)
    dtw, dth = text_w(date_str, font_date)
    gap     = 16
    total_h = dh + gap + dth
    text_y  = (480 - total_h) // 2
    draw.text((date_cx - dw  // 2, text_y),            day_str,  font=font_day,  fill=(255, 255, 255))
    draw.text((date_cx - dtw // 2, text_y + dh + gap), date_str, font=font_date, fill=(180, 180, 180))

    if show_mic:
        rgba = canvas.convert('RGBA')
        shazam_listener.draw_mic_icon(rgba, x=756, y=448, color=(220, 220, 220, 200), size=28)
        canvas = rgba.convert('RGB')

    return canvas


def make_plain_clock_screen(show_mic=False):
    canvas = Image.new('RGB', (800, 480), (0, 0, 0))
    return draw_clock_on_canvas(canvas, show_mic=show_mic)


def make_art_clock_screen(art, show_mic=False):
    canvas = art.resize((800, 480), Image.LANCZOS)
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=cfg["clock_blur_radius"]))
    overlay = Image.new('RGB', (800, 480), (0, 0, 0))
    canvas = Image.blend(canvas, overlay, alpha=0.3)
    return draw_clock_on_canvas(canvas, show_mic=show_mic)


# --- Art screen ---

def make_base_screen(art):
    canvas = Image.new('RGB', (800, 480), (0, 0, 0))
    bg = art.resize((800, 480), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=cfg["blur_radius"]))
    canvas.paste(bg, (0, 0))
    fg = art.resize((480, 480), Image.LANCZOS)
    canvas.paste(fg, (160, 0))
    return canvas

def draw_progress(canvas, seek_pos, length):
    if seek_pos is not None and length and length > 0:
        draw = ImageDraw.Draw(canvas)
        progress = seek_pos / length
        bh    = cfg["progress_bar_height"]
        bar_y = 480 - bh
        col   = hex_to_rgb(cfg["progress_bar_colour"])
        draw.rectangle([0, bar_y, 800, 480], fill=(40, 40, 40))
        draw.rectangle([0, bar_y, int(800 * progress), 480], fill=col)
    return canvas

def measure_text(text, font):
    dummy = Image.new('RGB', (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def make_text_overlay(artist, album, track, scroll_line=None, scroll_offset=0, source='roon'):
    font_artist = ImageFont.truetype(FONT_BOLD, cfg["size_artist"])
    font_album  = ImageFont.truetype(FONT_REG,  cfg["size_album"])
    font_track  = ImageFont.truetype(FONT_REG,  cfg["size_track"])

    aw, ah = measure_text(artist, font_artist)
    lw, lh = measure_text(album,  font_album)
    tw, th = measure_text(track,  font_track)

    ls = cfg["line_spacing"]
    display_w = max(aw, lw, tw)
    block_w   = display_w + PAD * 2
    block_h   = ah + lh + th + ls * 2 + PAD * 2

    x = MARGIN
    y = 480 - block_h - MARGIN - cfg["progress_bar_height"] - 4

    overlay = Image.new('RGBA', (800, 480), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        [x - 8, y - 8, x + block_w + 8, y + block_h + 8],
        fill=(0, 0, 0, cfg["text_bg_opacity"])
    )
    if cfg["text_bg_blur"] > 0:
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=cfg["text_bg_blur"]))
    overlay_draw = ImageDraw.Draw(overlay)

    def draw_line(text, font, ty, color, line_key):
        w, h = measure_text(text, font)
        tx = x + PAD
        if line_key == scroll_line and scroll_offset > 0:
            tmp = Image.new('RGBA', (w + 10, h + 4), (0, 0, 0, 0))
            tmp_draw = ImageDraw.Draw(tmp)
            tmp_draw.text((0, 0), text, font=font, fill=color)
            crop_w  = min(block_w - PAD * 2, w)
            cropped = tmp.crop((scroll_offset, 0, scroll_offset + crop_w, h + 4))
            overlay.paste(cropped, (tx, ty), cropped)
        else:
            overlay_draw.text((tx, ty), text, font=font, fill=color)

    ty = y + PAD
    draw_line(artist, font_artist, ty, (255, 255, 255, 255), 'artist')
    ty += ah + ls
    draw_line(album,  font_album,  ty, (220, 220, 220, 255), 'album')
    ty += lh + ls
    draw_line(track,  font_track,  ty, (220, 220, 220, 255), 'track')

    if source == 'shazam':
        font_icon = ImageFont.truetype(FONT_REG, 20)
        overlay_draw.text((754, 455), '♩', font=font_icon, fill=(180, 180, 180, 200))

    return overlay

def composite(base, overlay, seek_pos=None, length=None):
    canvas = base.copy().convert('RGBA')
    canvas = Image.alpha_composite(canvas, overlay)
    canvas = canvas.convert('RGB')
    if seek_pos is not None:
        canvas = draw_progress(canvas, seek_pos, length)
    return canvas


def display_roon(base, artist, album, track, seek_pos, length):
    font_artist = ImageFont.truetype(FONT_BOLD, cfg["size_artist"])
    font_album  = ImageFont.truetype(FONT_REG,  cfg["size_album"])
    font_track  = ImageFont.truetype(FONT_REG,  cfg["size_track"])

    aw, _ = measure_text(artist, font_artist)
    lw, _ = measure_text(album,  font_album)
    tw, _ = measure_text(track,  font_track)

    widths       = {'artist': aw, 'album': lw, 'track': tw}
    longest_line = max(widths, key=widths.get)
    longest_w    = widths[longest_line]
    overflow     = longest_w - MAX_TEXT_W

    overlay = make_text_overlay(artist, album, track, source='roon')
    write_fb(composite(base, overlay, seek_pos, length))

    if overflow > 0:
        time.sleep(cfg["scroll_hold"])
        total_frames = int(cfg["scroll_fps"] * cfg["scroll_speed"])
        frame_time   = 1.0 / cfg["scroll_fps"]
        aborted = False
        for frame in range(total_frames + 1):
            zone_check = get_zone()
            if not zone_check or zone_check["state"] != "playing":
                aborted = True
                break
            offset  = int(overflow * frame / total_frames)
            overlay = make_text_overlay(artist, album, track,
                                        scroll_line=longest_line,
                                        scroll_offset=offset,
                                        source='roon')
            write_fb(composite(base, overlay, seek_pos, length))
            time.sleep(frame_time)

        if not aborted:
            time.sleep(cfg["scroll_hold"])
            overlay = make_text_overlay(artist, album, track, source='roon')
            write_fb(composite(base, overlay, seek_pos, length))

    for _ in range(cfg["text_hold"]):
        zone_check = get_zone()
        if not zone_check or zone_check["state"] != "playing":
            break
        seek_pos = zone_check["now_playing"].get("seek_position")
        length   = zone_check["now_playing"].get("length")
        write_fb(composite(base, overlay, seek_pos, length))
        time.sleep(1)

    while True:
        zone_check = get_zone()
        if not zone_check or zone_check["state"] != "playing":
            break
        new_track = zone_check["now_playing"]["two_line"]["line1"]
        if new_track != track:
            break
        seek_pos = zone_check["now_playing"].get("seek_position")
        length   = zone_check["now_playing"].get("length")
        screen = base.copy()
        screen = draw_progress(screen, seek_pos, length)
        write_fb(screen)
        time.sleep(5)


def display_shazam(base, artist, album, track):
    font_artist = ImageFont.truetype(FONT_BOLD, cfg["size_artist"])
    font_album  = ImageFont.truetype(FONT_REG,  cfg["size_album"])
    font_track  = ImageFont.truetype(FONT_REG,  cfg["size_track"])

    aw, _ = measure_text(artist, font_artist)
    lw, _ = measure_text(album,  font_album)
    tw, _ = measure_text(track,  font_track)

    widths       = {'artist': aw, 'album': lw, 'track': tw}
    longest_line = max(widths, key=widths.get)
    longest_w    = widths[longest_line]
    overflow     = longest_w - MAX_TEXT_W

    overlay = make_text_overlay(artist, album, track, source='shazam')
    write_fb(composite(base, overlay))

    if overflow > 0:
        time.sleep(cfg["scroll_hold"])
        total_frames = int(cfg["scroll_fps"] * cfg["scroll_speed"])
        frame_time   = 1.0 / cfg["scroll_fps"]
        for frame in range(total_frames + 1):
            offset  = int(overflow * frame / total_frames)
            overlay = make_text_overlay(artist, album, track,
                                        scroll_line=longest_line,
                                        scroll_offset=offset,
                                        source='shazam')
            write_fb(composite(base, overlay))
            time.sleep(frame_time)

        time.sleep(cfg["scroll_hold"])
        overlay = make_text_overlay(artist, album, track, source='shazam')
        write_fb(composite(base, overlay))

    for _ in range(cfg["text_hold"]):
        time.sleep(1)

    # Show clean art with just ♩ symbol
    clean = base.copy()
    rgba  = clean.convert('RGBA')
    draw  = ImageDraw.Draw(rgba)
    font_icon = ImageFont.truetype(FONT_REG, 20)
    draw.text((754, 455), '♩', font=font_icon, fill=(180, 180, 180, 200))
    write_fb(rgba.convert('RGB'))


# --- Roon API ---

def get_zone():
    try:
        r = requests.get(f"{cfg['bridge']}/roonAPI/listZones", timeout=5)
        zones = r.json()["zones"]
        return next((z for z in zones if z["display_name"] == cfg["target_zone"]), None)
    except Exception as e:
        print(f"Error getting zone: {e}")
        return None

def get_art(image_key):
    try:
        r = requests.get(f"{cfg['bridge']}/roonAPI/getOriginalImage?image_key={image_key}", timeout=10)
        return Image.open(BytesIO(r.content))
    except Exception as e:
        print(f"Error getting art: {e}")
        return None


# --- Main loop ---

last_image_key    = None
last_track        = None
last_art          = None
clock_idle_since  = None
clock_last_min    = -1
shazam_active     = False
last_shazam_track = None  # dict of last displayed Shazam track
last_mic_state    = False

print("Starting Roon display...")
set_brightness(get_brightness_for_time())

while True:
    maybe_reload_config()
    zone = get_zone()

    if zone and zone["state"] == "playing" and "now_playing" in zone:
        if shazam_active:
            shazam_listener.stop()
            shazam_active     = False
            last_shazam_track = None
            last_mic_state    = False

        np_info   = zone["now_playing"]
        image_key = np_info.get("image_key")
        artist    = np_info["two_line"]["line2"]
        track     = np_info["two_line"]["line1"]
        album     = np_info.get("three_line", {}).get("line3", "")
        seek_pos  = np_info.get("seek_position")
        length    = np_info.get("length")

        set_brightness(get_brightness_for_time())
        clock_idle_since = None
        clock_last_min   = -1

        if image_key and (image_key != last_image_key or track != last_track):
            print(f"[Roon] Now playing: {artist} / {album} / {track}")
            new_art = get_art(image_key)
            if new_art:
                last_art       = new_art
                base           = make_base_screen(new_art)
                last_image_key = image_key
                last_track     = track
                display_roon(base, artist, album, track, seek_pos, length)

    else:
        if last_image_key is not None:
            print("Roon stopped")
            last_image_key   = None
            last_track       = None
            clock_idle_since = time.time()
            set_brightness(cfg["full_brightness"])
            clock_last_min   = -1

        if not shazam_active:
            shazam_listener.start()
            shazam_active = True

        mic_sampling  = shazam_listener.is_sampling()
        shazam_track  = shazam_listener.get_current_track()
        silence_secs  = shazam_listener.seconds_since_sound()

        # Only clear displayed track if genuinely silent
        if silence_secs > cfg["silence_timeout"] and last_shazam_track is not None:
            print(f"[Shazam] Silence {silence_secs:.0f}s — returning to clock")
            last_shazam_track = None
            clock_last_min    = -1

        # Only update display if we have a new track (different title/artist)
        if (shazam_track is not None
                and silence_secs <= cfg["silence_timeout"]
                and (last_shazam_track is None
                     or shazam_track['title'] != last_shazam_track['title']
                     or shazam_track['artist'] != last_shazam_track['artist'])):
            print(f"[Shazam] New track: {shazam_track['artist']} - {shazam_track['title']}")
            art = shazam_listener.fetch_art(shazam_track['art_url'])
            if art:
                last_art          = art
                last_shazam_track = shazam_track
                clock_idle_since  = None
                clock_last_min    = -1
                last_mic_state    = False
                base = make_base_screen(art)
                display_shazam(base,
                               shazam_track['artist'],
                               shazam_track['album'],
                               shazam_track['title'])

        elif last_shazam_track is None:
            # No match or silence — show clock
            if clock_idle_since is not None:
                if time.time() - clock_idle_since >= cfg["dim_after_secs"]:
                    set_brightness(get_brightness_for_time())

            now = datetime.now()
            if now.minute != clock_last_min or mic_sampling != last_mic_state:
                if last_art is not None:
                    write_fb(make_art_clock_screen(last_art, show_mic=mic_sampling))
                else:
                    write_fb(make_plain_clock_screen(show_mic=mic_sampling))
                clock_last_min = now.minute
                last_mic_state = mic_sampling

        time.sleep(2)
