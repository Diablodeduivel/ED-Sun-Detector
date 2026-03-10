#!/usr/bin/env python3
"""
ED Sun Detector
Detects Elite Dangerous stars on screen via per-star-type color analysis.
Outputs a JSON status file (for the SimHub C# plugin) and optionally
sends PWM values to an Arduino to control a fan.

pip install mss pillow numpy pyserial
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
import os
import json
import queue

try:
	import mss
	MSS_OK = True
except ImportError:
	MSS_OK = False

try:
	import numpy as np
	NP_OK = True
except ImportError:
	NP_OK = False

try:
	from PIL import Image, ImageTk
	PIL_OK = True
except ImportError:
	PIL_OK = False

try:
	import serial
	import serial.tools.list_ports
	SER_OK = True
except ImportError:
	SER_OK = False


# ── Config ────────────────────────────────────────────────────────────────────

STATUS_DIR  = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SunDetector")
STATUS_FILE = os.path.join(STATUS_DIR, "status.json")
os.makedirs(STATUS_DIR, exist_ok=True)

# Theme
BG        = "#080c10"
BG2       = "#0f1620"
BG3       = "#162030"
BORDER    = "#1e3048"
ACCENT    = "#ff8800"
ACCENT_LO = "#7a4000"
TEXT      = "#a8c0d0"
TEXT_DIM  = "#405060"
GREEN     = "#00e87a"
RED       = "#ff3050"


# ── Detection engine ──────────────────────────────────────────────────────────

class DetectionResult:
	__slots__ = ("detected", "intensity", "hot_fraction", "dominant_rgb", "preview")
	def __init__(self, detected, intensity, hot_fraction, dominant_rgb, preview=None):
		self.detected      = detected
		self.intensity     = intensity       # 0.0 – 1.0 clamped
		self.hot_fraction  = hot_fraction    # raw pixel fraction
		self.dominant_rgb  = dominant_rgb    # (r, g, b) 0–255
		self.preview       = preview         # PIL Image or None


class SunDetector:
	"""
	Detects ED stars on screen using per-star-type color profiles.

	Star color profiles based on in-game appearance:

	Main sequence (O, B, A, F, G, K, M):
	  O  - vivid blue     (52,000 K)  deep blue bloom
	  B  - blue-white     (10-30K K)  bright blue-white
	  A  - white          (7,500 K)   near-white, faint blue tint
	  F  - yellow-white   (6,000 K)   warm white
	  G  - yellow         (5,200 K)   our Sun — yellow-white
	  K  - orange         (3,700 K)   warm orange
	  M  - red-orange     (2,400 K)   reddish bloom

	Giants / Supergiants:   same hues as above, just larger bloom
	Proto stars (TTS/Herbig): warm orange-red, slightly nebulous

	Carbon stars (C, CN, CS, S, MS):
	  Deep red-orange, very saturated — r >> g, r >> b

	Wolf-Rayet (W, WC, WN, WO):
	  Intensely bright blue-green/blue-white

	Neutron stars:
	  Extreme white-blue spike, very small but blinding

	White dwarfs (DA, DB, DC, DO, DQ, DX):
	  Bright white, sometimes bluish — very small bloom

	Brown dwarfs (L, T, Y):
	  Very dim reddish-brown to magenta — low luminance,
	  high r vs b/g but weak signal. Detected at lower threshold.

	Black holes: No bloom — excluded intentionally.
	"""

	def __init__(self):
		self.sensitivity  = 0.04    # fraction of matching pixels needed (0–1)
		self.region_crop  = 0.28    # crop this fraction from each edge

	def grab_region(self, sct):
		"""Return absolute screen coordinates for the capture region."""
		mon = sct.monitors[1]
		m   = self.region_crop
		return {
			"left"  : mon["left"] + int(mon["width"]  * m),
			"top"   : mon["top"]  + int(mon["height"] * m),
			"width" : int(mon["width"]  * (1 - 2 * m)),
			"height": int(mon["height"] * (1 - 2 * m)),
		}

	def analyze(self, bgra: "np.ndarray") -> DetectionResult:
		# Split channels (mss returns BGRA)
		b = bgra[:, :, 0].astype(np.float32) / 255.0
		g = bgra[:, :, 1].astype(np.float32) / 255.0
		r = bgra[:, :, 2].astype(np.float32) / 255.0

		# Perceptual luminance
		lum = 0.2126 * r + 0.7152 * g + 0.0722 * b

		# ── Per-star-type color masks ─────────────────────────────────────

		# O-type: vivid blue, very bright (blue > red significantly)
		class_O = (lum > 0.75) & (b > 0.75) & (b > r + 0.15) & (b > g * 0.95)

		# B-type: blue-white, bright
		class_B = (lum > 0.75) & (b > 0.65) & (g > 0.65) & (b >= r)

		# A-type: near white, faint blue tint, very bright
		class_A = (lum > 0.82) & (b > 0.78) & (g > 0.78) & (r > 0.75) \
		        & (b - r < 0.20)

		# F-type: warm white / pale yellow-white
		class_F = (lum > 0.80) & (r > 0.82) & (g > 0.78) & (b > 0.65) \
		        & (r - b < 0.25)

		# G-type: yellow-white (our Sun)
		class_G = (lum > 0.72) & (r > 0.80) & (g > 0.72) & (b < 0.70) \
		        & (r - b > 0.10) & (r - b < 0.40)

		# K-type: orange
		class_K = (lum > 0.55) & (r > 0.75) & (g > 0.40) & (g < 0.75) \
		        & (b < 0.45) & (r - b > 0.35)

		# M-type: red-orange bloom
		class_M = (lum > 0.40) & (r > 0.65) & (r > g * 1.25) \
		        & (r > b * 1.60) & (b < 0.40)

		# Carbon stars (C, S, MS): deep saturated red, r >> g >> b
		class_C = (r > 0.55) & (r > g * 1.50) & (r > b * 2.20) \
		        & (g > b * 1.10) & (lum > 0.25)

		# Wolf-Rayet (W, WC, WN, WO): very bright blue-green to blue-white
		class_W = (lum > 0.80) & (b > 0.70) & (g > 0.65) \
		        & ((g > r * 1.05) | (b > r * 1.10))

		# Neutron star: extreme brightness spike, blue-white
		class_NS = (lum > 0.92) & (b > 0.85) & (g > 0.85) & (r > 0.82)

		# White dwarf (D subtypes): very bright, near-white to blue-white
		class_WD = (lum > 0.88) & (r > 0.82) & (g > 0.82) & (b > 0.80)

		# Brown dwarfs (L, T, Y): dim reddish-brown / magenta, low lum
		# L: dark red-magenta, T: reddish-brown, Y: very faint
		class_L  = (lum > 0.18) & (lum < 0.60) & (r > 0.35) \
		         & (r > g * 1.30) & (r > b * 1.10)

		# Proto stars (TTS, Herbig Ae/Be): warm orange with nebula haze
		class_proto = (lum > 0.45) & (r > 0.60) & (g > 0.35) \
		            & (r - b > 0.20) & (r - g < 0.40)

		# ── Combine all star profiles ─────────────────────────────────────
		sun_mask = (class_O | class_B | class_A | class_F | class_G |
		            class_K | class_M | class_C | class_W | class_NS |
		            class_WD | class_L | class_proto)

		# Require a minimum brightness floor to reject noise/dark UI elements
		sun_mask = sun_mask & (lum > 0.18)

		fraction  = float(np.mean(sun_mask))
		detected  = fraction >= self.sensitivity
		intensity = min(1.0, fraction / max(self.sensitivity * 2.5, 1e-6))

		# Dominant color of sun pixels (for swatch display)
		if np.any(sun_mask):
			dr = int(np.mean(r[sun_mask]) * 255)
			dg = int(np.mean(g[sun_mask]) * 255)
			db = int(np.mean(b[sun_mask]) * 255)
		else:
			dr, dg, db = 20, 35, 50

		return DetectionResult(
			detected     = detected,
			intensity    = intensity,
			hot_fraction = fraction,
			dominant_rgb = (dr, dg, db),
		)


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):

	PREVIEW_W = 220
	PREVIEW_H = 145

	def __init__(self):
		super().__init__()
		self.title("ED Sun Detector")
		self.configure(bg=BG)
		self.resizable(False, False)

		self.detector    = SunDetector()
		self.running     = False
		self.result_q    = queue.Queue(maxsize=2)
		self.ser_conn    = None
		self.last_state  = None

		self._style()
		self._build()
		self._check_deps()
		self._start()
		self.protocol("WM_DELETE_WINDOW", self._quit)

	# ── Theming ───────────────────────────────────────────────────────────────

	def _style(self):
		s = ttk.Style(self)
		s.theme_use("clam")
		s.configure("TCheckbutton", background=BG, foreground=TEXT,
		            font=("Consolas", 9))
		s.configure("TCombobox",    fieldbackground=BG2, background=BG2,
		            foreground=TEXT, font=("Consolas", 9))
		s.configure("TScale",       background=BG, troughcolor=BG3,
		            sliderthickness=12)
		s.map("TCheckbutton", background=[("active", BG)])

	# ── Layout ────────────────────────────────────────────────────────────────

	def _build(self):
		P = {"padx": 14, "pady": 0}

		# ── Header bar ──────────────────────────────────────────────────────
		hdr = tk.Frame(self, bg=BG2, height=40, highlightbackground=BORDER,
		               highlightthickness=1)
		hdr.pack(fill="x")
		hdr.pack_propagate(False)

		tk.Label(hdr, text="☀  ED SUN DETECTOR", font=("Consolas", 12, "bold"),
		         fg=ACCENT, bg=BG2).pack(side="left", padx=14, pady=6)

		self.hdr_dot = tk.Label(hdr, text="●", font=("Consolas", 16),
		                         fg=TEXT_DIM, bg=BG2)
		self.hdr_dot.pack(side="right", padx=14)
		self.hdr_txt = tk.Label(hdr, text="SCANNING", font=("Consolas", 9),
		                         fg=TEXT_DIM, bg=BG2)
		self.hdr_txt.pack(side="right")

		# ── Main content ────────────────────────────────────────────────────
		body = tk.Frame(self, bg=BG)
		body.pack(fill="both", padx=14, pady=10)

		# Left column: preview
		left = tk.Frame(body, bg=BG2, width=self.PREVIEW_W + 4,
		                highlightbackground=BORDER, highlightthickness=1)
		left.pack(side="left", anchor="n")
		left.pack_propagate(False)

		tk.Label(left, text="CAPTURE REGION", font=("Consolas", 7),
		         fg=TEXT_DIM, bg=BG2).pack(pady=(5, 0))

		self.preview = tk.Label(left, bg=BG2, width=self.PREVIEW_W,
		                         height=self.PREVIEW_H, text="awaiting signal…",
		                         fg=TEXT_DIM, font=("Consolas", 8))
		self.preview.pack(padx=2, pady=(2, 6))

		# Right column
		right = tk.Frame(body, bg=BG)
		right.pack(side="left", fill="both", expand=True, padx=(10, 0))

		# Intensity
		self._label(right, "INTENSITY")
		bar_bg = tk.Frame(right, bg=BG3, height=22,
		                  highlightbackground=BORDER, highlightthickness=1)
		bar_bg.pack(fill="x", pady=(2, 8))
		bar_bg.pack_propagate(False)

		self.int_bar = tk.Frame(bar_bg, bg=ACCENT_LO, height=22)
		self.int_bar.place(x=0, y=0, relheight=1, width=0)

		self.int_pct = tk.Label(bar_bg, text="0%", font=("Consolas", 9, "bold"),
		                         fg=TEXT, bg=BG3, anchor="e")
		self.int_pct.place(relx=1.0, rely=0.5, anchor="e", x=-5)

		# Color swatch
		self._label(right, "STAR COLOR")
		self.swatch = tk.Frame(right, bg="#142030", height=18,
		                        highlightbackground=BORDER, highlightthickness=1)
		self.swatch.pack(fill="x", pady=(2, 8))

		self.swatch_lbl = tk.Label(self.swatch, text="—", font=("Consolas", 7),
		                            fg=TEXT_DIM, bg="#142030")
		self.swatch_lbl.pack(side="right", padx=4)

		# Sensitivity
		self._label(right, "SENSITIVITY  (lower = trigger earlier)")
		sf = tk.Frame(right, bg=BG)
		sf.pack(fill="x", pady=(2, 8))

		self.sens_var = tk.DoubleVar(value=4.0)
		ttk.Scale(sf, from_=0.5, to=20.0, variable=self.sens_var,
		          orient="horizontal",
		          command=self._on_sens).pack(side="left", fill="x", expand=True)
		self.sens_lbl = tk.Label(sf, text="4.0%", font=("Consolas", 8),
		                          fg=TEXT, bg=BG, width=5, anchor="e")
		self.sens_lbl.pack(side="left")

		# ── Divider ─────────────────────────────────────────────────────────
		tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

		# ── Outputs ─────────────────────────────────────────────────────────
		out = tk.Frame(self, bg=BG)
		out.pack(fill="x", padx=14, pady=8)

		self._label(out, "OUTPUTS", bold=True)

		# SimHub row
		sh = tk.Frame(out, bg=BG)
		sh.pack(fill="x", pady=2)
		self.sh_var = tk.BooleanVar(value=True)
		ttk.Checkbutton(sh, text="SimHub JSON file", variable=self.sh_var).pack(side="left")
		path_short = STATUS_FILE.replace(os.environ.get("USERPROFILE", ""), "~")
		tk.Label(sh, text=f"→ {path_short}", font=("Consolas", 7),
		         fg=TEXT_DIM, bg=BG).pack(side="left", padx=8)

		# Serial row
		ser = tk.Frame(out, bg=BG)
		ser.pack(fill="x", pady=2)
		self.ser_var = tk.BooleanVar(value=False)
		ttk.Checkbutton(ser, text="Arduino serial",
		                variable=self.ser_var,
		                command=self._serial_toggle).pack(side="left")
		self.port_var = tk.StringVar(value="")
		self.port_cb  = ttk.Combobox(ser, textvariable=self.port_var,
		                              width=9, state="readonly")
		self.port_cb.pack(side="left", padx=4)
		tk.Button(ser, text="↻", font=("Consolas", 9), fg=TEXT_DIM, bg=BG3,
		          bd=0, padx=4, activebackground=BG3,
		          command=self._refresh_ports).pack(side="left")
		self._refresh_ports()

		test = tk.Frame(out, bg=BG)
		test.pack(fill="x", pady=4)
		self.test_btn = tk.Button(test, text="▶  TEST FAN  (3s full speed)",
		                          font=("Consolas", 9, "bold"),
		                          fg=BG, bg=ACCENT, activebackground=ACCENT_LO,
		                          bd=0, padx=10, pady=4,
		                          command=self._test_fan)
		self.test_btn.pack(side="left")
		self.test_lbl = tk.Label(test, text="", font=("Consolas", 8),
		                          fg=TEXT_DIM, bg=BG)
		self.test_lbl.pack(side="left", padx=10)

		# ── Status bar ──────────────────────────────────────────────────────
		tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
		self.log = tk.Label(self, text="Initializing…", font=("Consolas", 8),
		                     fg=TEXT_DIM, bg=BG, anchor="w")
		self.log.pack(fill="x", padx=14, pady=4)

	def _label(self, parent, text, bold=False):
		font = ("Consolas", 7, "bold") if bold else ("Consolas", 7)
		tk.Label(parent, text=text, font=font,
		         fg=TEXT_DIM, bg=BG).pack(anchor="w")

	# ── Controls ──────────────────────────────────────────────────────────────

	def _check_deps(self):
		missing = [n for n, ok in [("mss", MSS_OK), ("numpy", NP_OK), ("pillow", PIL_OK)] if not ok]
		if missing:
			self._log(f"⚠  pip install {' '.join(missing)}")

	def _on_sens(self, _=None):
		v = self.sens_var.get()
		self.sens_lbl.config(text=f"{v:.1f}%")
		self.detector.sensitivity = v / 100.0

	def _refresh_ports(self):
		if not SER_OK:
			return
		ports = [p.device for p in serial.tools.list_ports.comports()]
		self.port_cb["values"] = ports
		if ports and not self.port_var.get():
			self.port_var.set(ports[0])
		if hasattr(self, "sh_port_cb"):
			self.sh_port_cb["values"] = ports
			if ports and not self.sh_port_var.get():
				self.sh_port_var.set(ports[0])

	def _test_fan(self):
		if not self.ser_conn:
			# Auto-connect first if a port is selected
			self._serial_connect()
			if not self.ser_conn:
				self.test_lbl.config(text="⚠  connect Arduino first", fg=RED)
				return

		def run():
			steps = [255, 200, 150, 100, 50, 0]
			labels = ["Full speed…", "80%…", "60%…", "40%…", "20%…", "Done"]
			self.after(0, lambda: self.test_btn.config(state="disabled"))
			for i, (pwm, lbl) in enumerate(zip(steps, labels)):
				try:
					self.ser_conn.write(f"{pwm}\n".encode())
				except Exception as e:
					self.after(0, lambda e=e: self.test_lbl.config(text=f"Error: {e}", fg=RED))
					break
				self.after(0, lambda l=lbl, p=pwm: self.test_lbl.config(
					text=f"{l} (PWM {p})", fg=ACCENT if p > 0 else GREEN))
				time.sleep(0.5 if pwm > 0 else 0)
			self.after(0, lambda: self.test_btn.config(state="normal"))

		threading.Thread(target=run, daemon=True).start()

	def _serial_toggle(self):
		if self.ser_var.get():
			self._serial_connect()
		else:
			self._serial_disconnect()

	def _serial_connect(self):
		if not SER_OK:
			self._log("pyserial not installed"); return
		port = self.port_var.get()
		if not port:
			self._log("Select a COM port first"); return
		try:
			self.ser_conn = serial.Serial(port, 9600, timeout=1)
			time.sleep(1.5)
			self._log(f"Serial connected: {port}")
		except Exception as e:
			self._log(f"Serial error: {e}")
			self.ser_var.set(False)

	def _serial_disconnect(self):
		if self.ser_conn:
			try:
				self.ser_conn.write(b"0\n")
				self.ser_conn.close()
			except:
				pass
			self.ser_conn = None

	def _log(self, msg):
		self.log.config(text=msg)

	# ── Detection loop ────────────────────────────────────────────────────────

	def _start(self):
		if not all([MSS_OK, NP_OK, PIL_OK]):
			self._log("Install missing packages then restart")
			return
		self.running = True
		threading.Thread(target=self._thread, daemon=True).start()
		self._poll()

	def _thread(self):
		with mss.mss() as sct:
			while self.running:
				try:
					region = self.detector.grab_region(sct)
					shot   = sct.grab(region)
					arr    = np.frombuffer(shot.raw, dtype=np.uint8).reshape(
					             (shot.height, shot.width, 4))

					result = self.detector.analyze(arr)

					# Build preview thumbnail (BGRA → RGB)
					b_ch = arr[:, :, 0]
					g_ch = arr[:, :, 1]
					r_ch = arr[:, :, 2]
					rgb  = np.stack([r_ch, g_ch, b_ch], axis=2)
					img  = Image.fromarray(rgb.astype(np.uint8), "RGB")
					img  = img.resize((self.PREVIEW_W, self.PREVIEW_H), Image.LANCZOS)
					result.preview = img

					try:
						self.result_q.put_nowait(result)
					except queue.Full:
						try: self.result_q.get_nowait()
						except: pass
						self.result_q.put_nowait(result)

					time.sleep(0.08)  # ~12 fps

				except Exception as e:
					self.after(0, self._log, f"Capture error: {e}")
					time.sleep(1.0)

	def _poll(self):
		try:
			result: DetectionResult = self.result_q.get_nowait()
			self._apply(result)
		except queue.Empty:
			pass
		if self.running:
			self.after(80, self._poll)

	def _apply(self, r: DetectionResult):
		pct = int(r.intensity * 100)

		# Header status
		if r.detected:
			self.hdr_dot.config(fg=ACCENT)
			self.hdr_txt.config(text="SUN DETECTED", fg=ACCENT)
		else:
			self.hdr_dot.config(fg=TEXT_DIM)
			self.hdr_txt.config(text="CLEAR", fg=TEXT_DIM)

		# Intensity bar — get actual pixel width
		bar_bg_w = self.int_bar.master.winfo_width()
		bar_w    = max(0, int(r.intensity * bar_bg_w))
		bar_col  = ACCENT if r.detected else ACCENT_LO
		self.int_bar.config(width=bar_w, bg=bar_col)
		self.int_pct.config(text=f"{pct}%")

		# Color swatch
		dr, dg, db = r.dominant_rgb
		hex_col = f"#{dr:02x}{dg:02x}{db:02x}"
		self.swatch.config(bg=hex_col)
		self.swatch_lbl.config(bg=hex_col,
		                        text=f"R:{dr} G:{dg} B:{db}",
		                        fg="#000" if (dr + dg + db) > 380 else TEXT_DIM)

		# Preview
		if r.preview:
			try:
				tk_img = ImageTk.PhotoImage(r.preview)
				self.preview.config(image=tk_img, text="")
				self.preview._img = tk_img
			except:
				pass

		# SimHub JSON
		if self.sh_var.get():
			self._write_json(r)

		# Serial — PWM scales smoothly with how much sun fills the screen
		# Use a square root curve so small coverage still gives noticeable speed
		if r.detected:
			pwm_scale = min(1.0, (r.hot_fraction / 0.30) ** 0.5)  # 0.30 = full blast at 30% coverage
		else:
			pwm_scale = 0.0
		self._send_serial(pwm_scale)

		# Log only on state change
		if r.detected != self.last_state:
			self.last_state = r.detected
			verb = "☀  SUN ON" if r.detected else "○  SUN OFF"
			self._log(f"{verb} — {pct}% intensity  ({r.hot_fraction*100:.2f}% hot pixels)")

	def _write_json(self, r: DetectionResult):
		try:
			data = {
				"sun_detected" : 1 if r.detected else 0,
				"sun_intensity": round(r.intensity * 100, 1),
				"hot_fraction" : round(r.hot_fraction * 100, 3),
				"ts"           : time.time(),
			}
			with open(STATUS_FILE, "w") as f:
				json.dump(data, f)
		except:
			pass

	def _send_serial(self, intensity: float):
		if not (self.ser_var.get() and self.ser_conn):
			return
		try:
			pwm = int(intensity * 255)
			self.ser_conn.write(f"{pwm}\n".encode())
		except Exception as e:
			self._log(f"Serial write error: {e}")
			self._serial_disconnect()
			self.ser_var.set(False)

	def _quit(self):
		self.running = False
		self._serial_disconnect()
		self.destroy()


if __name__ == "__main__":
	app = App()
	app.mainloop()
