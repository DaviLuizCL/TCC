# SPDX-License-Identifier: AGPL-3.0-or-later
import cv2, yaml, argparse
from detector import PersonDetector
from utils import load_roi, center_of, DwellTimer, Cooldown, point_in_poly
from alerting import GpioAlerter, GpioConfig

def load_config(path: str):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def inside_roi(bbox, roi_poly):
    c = center_of(bbox)
    return point_in_poly(c, roi_poly)

def draw_overlay(frame, roi_poly, boxes, armed, triggered):
    overlay = frame.copy()
    # ROI
    import numpy as np
    if isinstance(roi_poly, list):
        roi_np = np.array(roi_poly, dtype=np.int32)
    else:
        roi_np = roi_poly
    cv2.polylines(overlay, [roi_np], True, (0, 255, 255), 2)
    # Caixas
    for b in boxes:
        (x1, y1, x2, y2) = b["bbox"]
        color = (0, 255, 0) if b.get("in_roi") else (200, 200, 200)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
    # Status
    txt = f"ARMADO: {'SIM' if armed else 'NAO'} | ALERT: {'SIM' if triggered else 'NAO'}"
    cv2.putText(overlay, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
    cv2.putText(overlay, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return overlay

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)

    # Vídeo
    src = cfg["video"]["source"]
    cap = cv2.VideoCapture(0 if (str(src) == "0") else src)
    if not cap.isOpened():
        raise RuntimeError("Não foi possível abrir a fonte de vídeo")

    # ROI
    roi_poly = load_roi(cfg["roi"]["file"])

    # Detector
    det = PersonDetector(
        weights=cfg["model"]["weights"],
        conf=cfg["model"]["conf"],
        iou=cfg["model"]["iou"],
        device=cfg["model"]["device"],
    )

    # Lógica
    armed = bool(cfg["alarm"]["enabled_start"])
    dwell = DwellTimer(cfg["alarm"]["dwell_seconds"])
    cooldown = Cooldown(cfg["alarm"]["cooldown_seconds"])
    min_box_area = int(cfg["alarm"]["min_box_area"])

    # GPIO
    gpio_cfg_dict = (cfg.get("outputs", {}).get("gpio", {}) or {})
    gpio_enabled = bool(gpio_cfg_dict.get("enabled", False))
    gpio_alerter = None
    if gpio_enabled:
        gc = GpioConfig(
            pin=int(gpio_cfg_dict.get("pin", 17)),
            setup=str(gpio_cfg_dict.get("setup", "BCM")),
            active_high=bool(gpio_cfg_dict.get("active_high", True)),
            mode=str(gpio_cfg_dict.get("mode", "pulse")),
            pulse_ms=int(gpio_cfg_dict.get("pulse_ms", 500)),
            clear_pin=(int(gpio_cfg_dict["clear_pin"]) if gpio_cfg_dict.get("clear_pin") is not None else None),
            clear_active_high=bool(gpio_cfg_dict.get("clear_active_high", True)),
            clear_pull=str(gpio_cfg_dict.get("clear_pull", "PUD_DOWN")),
            clear_debounce_ms=int(gpio_cfg_dict.get("clear_debounce_ms", 120)),
        )
        gpio_alerter = GpioAlerter(gc)

    resize_w = int(cfg["video"]["resize_width"])
    display = bool(cfg["video"]["display"])

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        h, w = frame.shape[:2]
        if resize_w and w > resize_w:
            scale = resize_w / w
            frame = cv2.resize(frame, None, fx=scale, fy=scale)
            h, w = frame.shape[:2]

        detections = det.detect(frame)
        detections = [d for d in detections if d["area"] >= min_box_area]

        any_in_roi = False
        boxes_in_roi = []
        for d in detections:
            in_roi = inside_roi(d["bbox"], roi_poly)
            d["in_roi"] = in_roi
            if in_roi:
                any_in_roi = True
                boxes_in_roi.append(d["bbox"])

        triggered = False

        # Disparo do alerta via GPIO (quando: armado, pessoa na ROI, cooldown ok, dwell atendido)
        if armed and any_in_roi and cooldown.ready():
            if dwell.update(True):
                if gpio_enabled and gpio_alerter is not None:
                    try:
                        gpio_alerter.trigger()
                    except Exception as e:
                        print("[GPIO] Falha ao disparar sinal:", e)
                cooldown.mark()
                triggered = True
        else:
            dwell.update(False)

        # LIMPEZA por pino de entrada (se configurado)
        if gpio_enabled and gpio_alerter is not None:
            try:
                if gpio_alerter.check_clear_input():
                    gpio_alerter.clear()
                    print("[GPIO] LATCH limpo via pino de limpeza.")
            except Exception as e:
                print("[GPIO] Erro ao verificar pino de limpeza:", e)

        if display:
            vis = draw_overlay(frame, roi_poly, detections, armed, triggered)
            cv2.imshow("PoolGuard", vis)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("a"):
                armed = not armed
                print("ARMADO:", armed)
            # tecla 'c' limpa latch também
            if key == ord("c") and gpio_enabled and gpio_alerter is not None:
                try:
                    gpio_alerter.clear()
                    print("[GPIO] LATCH limpo (nível inativo) por teclado.")
                except Exception as e:
                    print("[GPIO] Falha ao limpar latch:", e)

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
