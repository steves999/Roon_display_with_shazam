# Roon Display

A Raspberry Pi Zero 2W + HyperPixel 4 rectangular display showing album art for Roon and vinyl/radio via Shazam, with an analogue clock idle screen.

## Features

- **Roon integration** — album art, artist/album/track text, progress bar via REST bridge
- **Shazam vinyl/radio detection** — USB mic samples audio, identifies track, displays art
- **Analogue clock** — blurred album art background, date display
- **Web config UI** — adjust all settings via browser at `http://[pi-ip]:8080`
- **PWM backlight** — day/evening/night brightness levels
- **Battery powered** — IP5328P power bank module with LiPo cell

## Hardware

- Raspberry Pi Zero 2W
- Pimoroni HyperPixel 4.0 Rectangular (non-touch)
- USB microphone (via OTG adapter)
- IP5328P power bank module
- LiPo cell (126090 8000mAh or similar)

## Dependencies

### Pi Zero 2W (display)
```bash
sudo apt install -y python3-pip sox libsox-fmt-all ffmpeg libopenblas0 libportaudio2 libasound2-dev pigpio python3-pigpio python3-flask python3-numpy fonts-roboto tex-gyre fontconfig
pip3 install shazamio sounddevice --break-system-packages
```

### PiHole Pi (REST bridge)
```bash
sudo apt install -y nodejs npm git
git clone https://github.com/st0g1e/roon-extension-http-api.git
cd roon-extension-http-api
npm install
```

## Setup

### 1. REST Bridge (PiHole Pi)
```bash
sudo nano /etc/systemd/system/roon-bridge.service
```
```ini
[Unit]
Description=Roon HTTP API Bridge
After=network.target

[Service]
WorkingDirectory=/home/pi/roon-extension-http-api
ExecStart=/usr/bin/node server.js
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable roon-bridge
sudo systemctl start roon-bridge
```
Enable in Roon Settings → Extensions.

### 2. HyperPixel (Pi Zero 2W)
Add to `/boot/firmware/config.txt`:
```
dtoverlay=vc4-kms-dpi-hyperpixel4,rotate=270
dtparam=audio=off
```

### 3. ALSA config for USB mic
Create `~/.asoundrc`:
```
pcm.!default {
    type hw
    card 0
    device 0
}
ctl.!default {
    type hw
    card 0
}
```

### 4. Display service
```bash
sudo nano /etc/systemd/system/roon-display.service
```
```ini
[Unit]
Description=Roon Display
After=network.target pigpiod.service

[Service]
ExecStartPre=/usr/bin/chvt 1
ExecStart=/usr/bin/python3 /home/roondisplay2/roon_display.py
Restart=always
User=roondisplay2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable roon-display pigpiod
sudo systemctl start roon-display
```

### 5. Web config service
```bash
sudo nano /etc/systemd/system/roon-web.service
```
```ini
[Unit]
Description=Roon Display Web Config
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/roondisplay2/web_config.py
Restart=always
User=roondisplay2

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable roon-web
sudo systemctl start roon-web
```

### 6. Sudo permissions for web UI shutdown/restart
```bash
sudo visudo
```
Add:
```
roondisplay2 ALL=(ALL) NOPASSWD: /sbin/shutdown, /bin/systemctl
```

## Files

| File | Description |
|------|-------------|
| `roon_display.py` | Main display script |
| `shazam_listener.py` | Audio detection and Shazam recognition module |
| `web_config.py` | Flask web configuration UI |

## Configuration

All settings are stored in `~/config.json` and editable via the web UI at `http://[pi-ip]:8080`.

Key settings:
- `target_zone` — Roon zone name to monitor
- `bridge` — REST bridge IP and port
- `silence_threshold` — RMS level below which is silence (tune for your room)
- `silence_timeout` — seconds of silence before returning to clock
- `sample_interval` — seconds between Shazam samples

## Display Behaviour

```
Roon playing → album art + text + progress bar
Roon stopped + music detected → Shazam art + text + ♩ indicator
Roon stopped + silence → analogue clock over last album art
```
