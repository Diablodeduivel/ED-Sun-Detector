# ED Sun Detector 🌟

A Python desktop app that watches your screen while playing **Elite Dangerous** and detects when you're near a star — then does something physical about it.

The original use case: drive a **fan via Arduino** to blast air at you when flying close to a sun, and feed **intensity data into SimHub** for tactile feedback (bass shakers / ButtKickers). But the architecture is modular — you can use just the JSON output, just the Arduino, or both.

![status: working](https://img.shields.io/badge/status-working-brightgreen)
![python: 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![platform: windows](https://img.shields.io/badge/platform-windows-lightgrey)

---

## How it works

Every ~80ms the app captures the center 44% of your screen and runs each pixel through a set of color profiles — one per star class in Elite Dangerous. If enough pixels match a known star color, it reports a detection with an intensity value from 0–100%.

Star color profiles cover all classes from the game:

| Class | Color | Examples |
|---|---|---|
| O | Vivid blue | Rare blue giants |
| B | Blue-white | Hot main sequence |
| A | Near-white | Sirius-type |
| F | Warm white | Slightly yellow-white |
| G | Yellow | Sol — our Sun |
| K | Orange | Very common, great for fuel scooping |
| M | Red-orange | Most common star in the galaxy |
| C / Carbon | Deep saturated red | Carbon stars |
| W / Wolf-Rayet | Bright blue-green | Rare, dangerous |
| Neutron | Blinding white spike | High-jump highway stars |
| White Dwarf | Bright white | Small and punchy |
| Brown Dwarf (L/T/Y) | Dim reddish | Not scoopable |
| Proto (TTS/Herbig) | Warm orange haze | Young stellar objects |

Black holes produce no visible bloom and are intentionally excluded.

---

## Outputs

### 1. SimHub JSON file (always on)

Writes to `%APPDATA%\SunDetector\status.json` every frame:

```json
{
  "sun_detected": 1,
  "sun_intensity": 72.4,
  "hot_fraction": 8.231,
  "ts": 1741600000.0
}
```

This is read by a companion **SimHub C# plugin** (included) which exposes three properties you can bind to any SimHub effect:

- `[SunDetectorPlugin.SunDetected]` — 0 or 1
- `[SunDetectorPlugin.SunIntensity]` — 0.0 to 100.0
- `[SunDetectorPlugin.HotFraction]` — raw hot pixel percentage

### 2. Arduino PWM fan (optional)

Connect a 4-pin PWM fan to an Arduino. The app sends a value 0–255 over serial and the Arduino sets the fan speed. Great for immersion when fuel scooping or flying close to a neutron star.

---

## What you need

### Minimum (just the JSON / SimHub output)
- Python 3.9+
- Elite Dangerous running on your primary monitor
- `pip install mss pillow numpy`

### For Arduino fan control (optional)
- Any Arduino (Uno, Nano, etc.)
- A 4-pin PWM PC fan
- `pip install pyserial`

### For SimHub tactile feedback (optional)
- SimHub installed
- The included C# plugin built and copied into SimHub

---

## Installation

### Python app

```bash
pip install mss pillow numpy pyserial
python ed_sun_detector.py
```

The app starts capturing immediately. You'll see a live preview, a star color swatch, and an intensity bar.

### Arduino sketch

1. Open `ed_fan_control.ino` in the Arduino IDE
2. Upload to your Arduino
3. Wire your fan's PWM wire (blue) to **pin 9**, fan power to 12V, GND to GND and Arduino GND
4. In the app, tick **Arduino serial**, pick your COM port, done

Tune `MIN_SPEED` in the sketch if your fan stalls at low values — typically around 70–90 for most fans.

### SimHub plugin

> Only needed if you want to bind sun intensity to SimHub effects (bass shakers, LEDs, etc.)

1. Build the plugin:
```
cd SunDetectorPlugin
dotnet build -c Release
```

2. Copy the DLL (run as admin):
```
copy "bin\Release\net48\SunDetectorPlugin.dll" "C:\Program Files (x86)\SimHub\"
```

3. Restart SimHub. The plugin appears automatically.

4. In SimHub → ShakeIt / LED effects, add a custom effect and use the formula:
```
[SunDetectorPlugin.SunIntensity]
```

---

## App UI

- **Live preview** — center 44% of your screen, what the detector actually sees
- **Intensity bar** — how strongly a star is detected (0–100%)
- **Star color swatch** — dominant color of the detected pixels (helps you see which star profile matched)
- **Sensitivity slider** — lower = triggers earlier/easier; default 4%
- **SimHub JSON toggle** — enable/disable writing the status file
- **Arduino serial toggle** — pick a COM port and connect your fan controller
- **Test fan button** — ramps from full speed down to 0 so you can verify wiring

---

## Tuning tips

- If it fires on bright UI elements or white nebulae, **raise the sensitivity** slider slightly
- If it misses dim red/brown dwarfs, **lower the sensitivity**
- The swatch color tells you which profile matched — if something non-star is triggering, note the RGB values and open an issue
- Fan starts ramping from the moment any star pixels appear; full speed kicks in around 30% screen coverage (square root curve)

---

## File overview

```
ed_sun_detector.py        Python GUI app — run this
ed_fan_control.ino        Arduino sketch for PWM fan
SunDetectorPlugin/
  SunDetectorPlugin.cs    SimHub C# plugin source
  SunDetectorPlugin.csproj
README.md
```

---

## Credits

Built for personal use flying Elite Dangerous with a ButtKicker tactile transducer and a desk fan wired to an Arduino Uno. Star color profiles researched from the [Elite Dangerous wiki](https://elite-dangerous.fandom.com/wiki/Stars).

Feel free to fork, adapt, or file issues if a star type isn't detecting correctly.
