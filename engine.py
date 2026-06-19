# SPDX-License-Identifier: AGPL-3.0-or-later
"""engine.py — motor de detecção do PoolGuard (1 instância por câmera).

Cada DetectionEngine roda o loop YOLO + ROI numa thread e expõe o último frame
anotado (stream), o status e controles. Ao decidir que deve alertar, chama um
callback `on_alert(camera_name, snapshot_jpeg)` — quem realmente aciona GPIO e
notificações é o AlertSink (compartilhado por todas as câmeras, pois o GPIO é
um hardware só).

Decisão de alerta (classificação criança/adulto + horário), avaliada na ROI:
  - criança sozinha                       -> alerta
  - criança + adulto, horário permitido   -> não alerta
  - criança + adulto, horário não permit. -> alerta
  - adulto sozinho / vazio                -> não alerta
Tudo configurável (classify.*). Classificação por altura aparente (heurística).

Robustez: reconexão de câmera, backend configurável (auto/v4l2/any, e RTSP via
URL), frame-skip (infer_every), log de eventos + snapshot em events/.
"""

from __future__ import annotations

import os
import time
import datetime
import threading

import cv2
import numpy as np

from detector import PersonDetector
from utils import load_roi, center_of, point_in_poly, DwellTimer, Cooldown, MotionGate
from alerting import GpioAlerter, GpioConfig
from notifier import Notifier

_BACKENDS = {"auto": cv2.CAP_ANY, "any": cv2.CAP_ANY, "v4l2": cv2.CAP_V4L2}

# Para RTSP, força transporte TCP (menos travamento) se o usuário não definiu nada.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def _norm_source(src):
    """Aceita 0, '0', '/dev/video2', 'video.mp4', 'rtsp://...'."""
    if isinstance(src, int):
        return src
    s = str(src).strip()
    return int(s) if s.isdigit() else s


def _parse_hm(s: str) -> datetime.time:
    h, m = str(s).split(":")
    return datetime.time(int(h), int(m))


def _now_in_windows(windows) -> bool:
    """True se o horário atual cai em alguma janela (suporta cruzar meia-noite)."""
    if not windows:
        return True  # sem janelas definidas = sempre permitido
    now = datetime.datetime.now().time()
    for w in windows:
        try:
            s, e = _parse_hm(w.get("start", "00:00")), _parse_hm(w.get("end", "23:59"))
        except Exception:
            continue
        if s <= e:
            if s <= now <= e:
                return True
        else:  # janela atravessa a meia-noite (ex.: 22:00–06:00)
            if now >= s or now <= e:
                return True
    return False


def draw_overlay(frame, roi, boxes, armed, triggered, classify=False, info=""):
    overlay = frame.copy()
    if roi is not None:
        cv2.polylines(overlay, [np.asarray(roi, dtype=np.int32)], True, (0, 255, 255), 2)
    for b in boxes:
        x1, y1, x2, y2 = b["bbox"]
        kind = b.get("kind")
        if classify and kind:
            base = (255, 0, 255) if kind == "child" else (0, 200, 0)  # criança=magenta, adulto=verde
        else:
            base = (0, 255, 0)
        color = base if b.get("in_roi") else tuple(int(c * 0.45) for c in base)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
        if classify and kind:
            lab = "crianca" if kind == "child" else "adulto"
            cv2.putText(overlay, lab, (x1, max(0, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    head = f"ARMADO:{'SIM' if armed else 'NAO'} ALERT:{'SIM' if triggered else 'NAO'}"
    if info:
        head += " | " + info
    cv2.putText(overlay, head, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
    cv2.putText(overlay, head, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return overlay


class DetectionEngine:
    def __init__(self, cfg: dict, name="Câmera", source=0, roi_path="roi_pool.yaml",
                 on_alert=None, on_alarm=None, events_dir="events"):
        self.name = name
        self.on_alert = on_alert            # borda de subida: notificações + snapshot
        self.on_alarm = on_alarm            # estado contínuo: liga/desliga a sirene (GPIO)
        self._alarm_active = False
        self.roi_path = roi_path
        self.events_dir = events_dir
        self._source_override = source

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._frame_jpeg = None
        self._raw_frame = None
        self._pending_cfg = None
        self._pending_roi = None

        self.status = {
            "name": name, "running": False, "armed": False, "triggered": False,
            "detections": 0, "in_roi": 0, "children": 0, "adults": 0,
            "allowed": True, "fps": 0.0, "infer_fps": 0.0, "camera": "iniciando",
            "source": str(source), "last_event": None,
        }

        self._armed = bool(cfg.get("alarm", {}).get("enabled_start", True))
        self._apply_cfg(cfg)

    # ---------- configuração ----------
    def _apply_cfg(self, cfg: dict):
        self.cfg = cfg
        m = cfg["model"]
        self.det = PersonDetector(weights=m["weights"], conf=float(m["conf"]),
                                  iou=float(m["iou"]), device=m["device"],
                                  imgsz=int(m.get("imgsz", 320)))
        a = cfg["alarm"]
        self.dwell = DwellTimer(float(a.get("dwell_grace_seconds", 0.6)))
        self.dwell_default = float(a["dwell_seconds"])
        # dwell mais curto para o caso crítico (criança sozinha); cai no padrão se ausente
        self.dwell_child_alone = float(a.get("dwell_child_alone_seconds", a["dwell_seconds"]))
        self.cooldown = Cooldown(float(a["cooldown_seconds"]))
        self.min_box_area = int(a["min_box_area"])

        v = cfg.get("video", {}) or {}
        self.source = _norm_source(self._source_override)
        self.backend = str(v.get("backend", "auto")).lower()
        self.resize_w = int(v.get("resize_width", 0))
        self.infer_every = max(1, int(v.get("infer_every", 1)))

        # Porteiro de movimento: só aciona o YOLO quando algo mexe na ROI.
        mo = cfg.get("motion", {}) or {}
        self.motion = MotionGate(
            enabled=bool(mo.get("enabled", True)),
            diff_thresh=int(mo.get("diff_thresh", 18)),
            min_motion_frac=float(mo.get("min_motion_frac", 0.004)),
            person_hold_s=float(mo.get("person_hold_seconds", 4.0)),
            motion_hold_s=float(mo.get("motion_hold_seconds", 1.5)),
            heartbeat_s=float(mo.get("heartbeat_seconds", 2.0)),
        )

        c = cfg.get("classify", {}) or {}
        self.classify_enabled = bool(c.get("enabled", False))
        self.child_height_frac = float(c.get("child_height_frac", 0.45))
        self.allowed_hours = c.get("allowed_hours", []) or []
        self.alert_child_alone = bool(c.get("alert_child_alone", True))
        self.alert_child_adult_outside = bool(c.get("alert_child_with_adult_outside_hours", True))

        self.roi = load_roi(self.roi_path) if os.path.exists(self.roi_path) else None

        n = cfg.get("notify", {}) or {}
        self.notify_snapshot = bool(n.get("snapshot", True))

        with self._lock:
            self.status["armed"] = self._armed
            self.status["source"] = str(self.source)

    # ---------- captura ----------
    def _open_cap(self):
        api = _BACKENDS.get(self.backend, cv2.CAP_ANY)
        cap = cv2.VideoCapture(self.source, api)
        try:
            # buffer mínimo: pega sempre o frame mais recente (crucial p/ RTSP,
            # que senão acumula segundos de vídeo atrasado = latência no alerta)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    # ---------- classificação + decisão ----------
    def _classify(self, dets, frame_h):
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            frac = (y2 - y1) / max(1, frame_h)
            d["kind"] = "child" if frac < self.child_height_frac else "adult"

    def _decide(self, dets_in_roi):
        """Retorna (deve_alertar, children, adults, allowed, caso)."""
        if not self.classify_enabled:
            n = len(dets_in_roi)
            return (n > 0, 0, 0, True, "person" if n else "none")
        children = sum(1 for d in dets_in_roi if d.get("kind") == "child")
        adults = sum(1 for d in dets_in_roi if d.get("kind") == "adult")
        allowed = _now_in_windows(self.allowed_hours)
        if children == 0:
            return (False, children, adults, allowed, "none")        # adulto sozinho / vazio
        if adults == 0:
            return (self.alert_child_alone, children, adults, allowed, "child_alone")
        # criança + adulto
        alert = (not allowed) and self.alert_child_adult_outside
        return (alert, children, adults, allowed, "child_adult_outside")

    # ---------- loop ----------
    def _loop(self):
        cap = self._open_cap()
        last_dets = []
        last_person = False
        frame_i = 0
        fps_t, fps_n, infer_n = time.time(), 0, 0
        enc_t = 0.0
        stream_min_dt = 1.0 / max(1, int((self.cfg.get("video", {}) or {}).get("stream_fps", 12)))

        while not self._stop.is_set():
            if self._pending_cfg is not None:
                with self._lock:
                    pend, self._pending_cfg = self._pending_cfg, None
                old_src = self.source
                self._apply_cfg(pend)
                if self.source != old_src:
                    cap.release()
                    cap = self._open_cap()
                    last_dets = []
            if self._pending_roi is not None:
                with self._lock:
                    self.roi, self._pending_roi = self._pending_roi, None

            if cap is None or not cap.isOpened():
                self._mark_camera("reconectando")
                cap = self._open_cap()
                time.sleep(1.0)
                continue
            ok, frame = cap.read()
            if not ok:
                self._mark_camera("reconectando")
                cap.release()
                time.sleep(1.0)
                cap = self._open_cap()
                continue
            self._mark_camera("ok")

            h, w = frame.shape[:2]
            if self.resize_w and w > self.resize_w:
                scale = self.resize_w / w
                frame = cv2.resize(frame, None, fx=scale, fy=scale)
                h, w = frame.shape[:2]

            # Porteiro de movimento decide se vale acionar o YOLO neste frame.
            now = time.time()
            gate_run = self.motion.should_run(frame, self.roi, last_person, now)
            if gate_run and frame_i % self.infer_every == 0:
                dets = [d for d in self.det.detect(frame) if d["area"] >= self.min_box_area]
                if self.classify_enabled:
                    self._classify(dets, h)
                last_dets = dets
                last_person = len(dets) > 0
                infer_n += 1
            elif not self.motion.active:
                # gate ocioso confirmado: ninguém na cena, descarta boxes antigas
                dets = last_dets = []
                last_person = False
            else:
                dets = last_dets  # janela de hold: reaproveita a última detecção
            frame_i += 1

            in_roi_dets = []
            for d in dets:
                d["in_roi"] = self.roi is not None and point_in_poly(center_of(d["bbox"]), self.roi)
                if d["in_roi"]:
                    in_roi_dets.append(d)

            should, children, adults, allowed, case = self._decide(in_roi_dets)

            # dwell acumula com base na presença (independe do cooldown); o limiar
            # é menor no caso crítico de criança sozinha.
            present = self._armed and should
            elapsed = self.dwell.update(present)
            need = self.dwell_child_alone if case == "child_alone" else self.dwell_default

            # Alarme SUSTENTADO: fica ativo enquanto a condição persistir (após o
            # dwell) e cai sozinho quando a criança sai (o grace do dwell evita
            # piscar em falhas breves de detecção). A sirene (GPIO) segue esse
            # estado; as notificações de celular saem 1x por episódio (cooldown).
            alarm_on = self._armed and elapsed >= need
            rising = alarm_on and not self._alarm_active
            if rising and self.cooldown.ready():
                self._fire(frame, dets)   # snapshot + notificações (borda de subida)
                self.cooldown.mark()
            self._alarm_active = alarm_on
            if self.on_alarm is not None:
                self.on_alarm(self.name, alarm_on)   # liga/desliga a sirene (deduplicado no sink)
            triggered = alarm_on

            info = ""
            if self.classify_enabled:
                info = f"C:{children} A:{adults} {'PERMITIDO' if allowed else 'FORA'}"

            # O preview não precisa da taxa total de captura: codifica o JPEG
            # anotado no máximo a stream_fps (poupa CPU do Pi). O frame "raw" é
            # guardado cru e só vira JPEG sob demanda em get_raw() (/api/snapshot).
            do_encode = triggered or (now - enc_t) >= stream_min_dt
            if do_encode:
                vis = draw_overlay(frame, self.roi, dets, self._armed, triggered,
                                   classify=self.classify_enabled, info=info)
                oka, ann = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 70])
                enc_t = now
            with self._lock:
                self._raw_frame = frame
                if do_encode and oka:
                    self._frame_jpeg = ann.tobytes()
                self.status.update(triggered=triggered, detections=len(dets),
                                   in_roi=len(in_roi_dets), children=children,
                                   adults=adults, allowed=allowed)

            fps_n += 1
            dt = time.time() - fps_t
            if dt >= 1.0:
                with self._lock:
                    self.status["fps"] = round(fps_n / dt, 1)
                    self.status["infer_fps"] = round(infer_n / dt, 1)
                fps_t, fps_n, infer_n = time.time(), 0, 0

        if cap is not None:
            cap.release()
        # ao parar, garante que a sirene não fique presa ligada
        self._alarm_active = False
        if self.on_alarm is not None:
            self.on_alarm(self.name, False)
        with self._lock:
            self.status["running"] = False
            self.status["camera"] = "parado"

    def _fire(self, frame, dets):
        ts = datetime.datetime.now()
        snap = None
        if self.notify_snapshot:
            vis = draw_overlay(frame, self.roi, dets, True, True,
                               classify=self.classify_enabled)
            ok, buf = cv2.imencode(".jpg", vis)
            if ok:
                snap = buf.tobytes()
                try:
                    os.makedirs(self.events_dir, exist_ok=True)
                    fname = os.path.join(self.events_dir,
                                         ts.strftime("%Y%m%d_%H%M%S_") + self.name.replace(" ", "_") + ".jpg")
                    with open(fname, "wb") as f:
                        f.write(snap)
                except Exception as e:
                    print("[EVENT] Falha ao salvar snapshot:", e)

        if self.on_alert is not None:
            threading.Thread(target=self.on_alert, args=(self.name, snap), daemon=True).start()
        with self._lock:
            self.status["last_event"] = ts.isoformat(timespec="seconds")
        print(f"[EVENT] {ts.isoformat(timespec='seconds')} ALERTA [{self.name}]")

    def _mark_camera(self, state):
        with self._lock:
            if self.status["camera"] != state:
                self.status["camera"] = state

    # ---------- API pública ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        with self._lock:
            self.status["running"] = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def set_armed(self, value: bool):
        self._armed = bool(value)
        with self._lock:
            self.status["armed"] = self._armed

    def apply_config(self, cfg: dict):
        self._armed = bool(cfg.get("alarm", {}).get("enabled_start", self._armed))
        with self._lock:
            self._pending_cfg = cfg

    def set_roi(self, points):
        with self._lock:
            self._pending_roi = np.array(points, dtype=np.int32)

    def get_frame(self):
        with self._lock:
            return self._frame_jpeg

    def get_raw(self):
        # Codifica o frame cru só quando alguém pede (ex.: /api/snapshot),
        # evitando um cv2.imencode por frame no loop principal.
        with self._lock:
            frame = self._raw_frame
        if frame is None:
            return None
        ok, raw = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return raw.tobytes() if ok else None

    def get_status(self):
        with self._lock:
            return dict(self.status)


class AlertSink:
    """Dono único do GPIO + Notifier, compartilhado por todas as câmeras.

    O GPIO é um hardware só, então centralizamos aqui: qualquer câmera que dispare
    aciona o mesmo pino e o mesmo fan-out de notificações.
    """

    def __init__(self, cfg: dict):
        self.gpio = None
        self.notifier = None
        self._alarm_lock = threading.Lock()
        self._active_cams = set()   # câmeras que querem a sirene ligada agora
        self._gpio_on = False       # último nível enviado ao GPIO (evita reescrever)
        self.reconfigure(cfg)

    def reconfigure(self, cfg: dict):
        self.notifier = Notifier(cfg.get("notify", {}) or {})
        self._gpio_on = False       # gpio recriado abaixo nasce inativo; reassere no próximo estado
        gd = (cfg.get("outputs", {}) or {}).get("gpio", {}) or {}
        if self.gpio is not None:
            try:
                self.gpio.cleanup()
            except Exception:
                pass
            self.gpio = None
        if bool(gd.get("enabled", False)):
            try:
                self.gpio = GpioAlerter(GpioConfig(
                    pin=int(gd.get("pin", 17)), setup=str(gd.get("setup", "BCM")),
                    active_high=bool(gd.get("active_high", True)),
                    mode=str(gd.get("mode", "pulse")), pulse_ms=int(gd.get("pulse_ms", 500)),
                    clear_pin=(int(gd["clear_pin"]) if gd.get("clear_pin") is not None else None),
                    clear_active_high=bool(gd.get("clear_active_high", True)),
                    clear_pull=str(gd.get("clear_pull", "PUD_DOWN")),
                    clear_debounce_ms=int(gd.get("clear_debounce_ms", 120)),
                ))
            except Exception as e:
                print("[GPIO] Falha ao inicializar:", e)
                self.gpio = None

    def set_alarm(self, camera_name, active):
        """Liga/desliga a sirene de forma contínua. A sirene fica ligada se QUALQUER
        câmera estiver em alarme (o GPIO é um hardware só, compartilhado)."""
        with self._alarm_lock:
            if active:
                self._active_cams.add(camera_name)
            else:
                self._active_cams.discard(camera_name)
            any_on = bool(self._active_cams)
            changed = any_on != self._gpio_on
            self._gpio_on = any_on
        if changed and self.gpio is not None:
            try:
                self.gpio.set_active(any_on)
            except Exception as e:
                print("[GPIO] Falha ao ajustar sirene:", e)

    def notify_alert(self, camera_name, snapshot=None):
        """Dispara as notificações (celular) — chamado 1x na borda de subida do alarme."""
        if self.notifier is not None:
            title = f"{self.notifier.title} [{camera_name}]"
            self.notifier.dispatch(title=title, snapshot=snapshot)

    def poll_clear(self):
        if self.gpio is not None:
            try:
                if self.gpio.check_clear_input():
                    self.clear()
                    print("[GPIO] Sirene silenciada via pino de limpeza.")
            except Exception:
                pass

    def clear(self):
        # silencia a sirene agora; se uma câmera ainda estiver em alarme, ela
        # voltará a ligar no próximo frame (não dá pra silenciar perigo ativo).
        with self._alarm_lock:
            self._active_cams.clear()
            self._gpio_on = False
        if self.gpio is not None:
            self.gpio.clear()

    def cleanup(self):
        if self.gpio is not None:
            try:
                self.gpio.cleanup()
            except Exception:
                pass

    def channel_names(self):
        return self.notifier.channel_names() if self.notifier else []
