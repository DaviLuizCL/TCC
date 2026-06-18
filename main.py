# SPDX-License-Identifier: AGPL-3.0-or-later
"""main.py — viewer desktop opcional (debug local, 1 câmera).

Usa o mesmo DetectionEngine + AlertSink do app web, mostrando o vídeo numa
janela OpenCV local. Para o uso normal (multi-câmera, config pelo navegador),
use o app web:  python server.py

Teclas:  A = armar/desarmar   C = limpar alerta   Q = sair
"""

import argparse

import cv2
import numpy as np
import yaml

from engine import DetectionEngine, AlertSink


def load_config(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    cams = cfg.get("cameras") or [{
        "name": "Câmera 1", "source": cfg.get("video", {}).get("source", 0),
        "roi_file": cfg.get("roi", {}).get("file", "roi_pool.yaml"),
    }]
    cam = cams[0]  # desktop mostra só a primeira

    sink = AlertSink(cfg)
    engine = DetectionEngine(cfg, name=cam.get("name", "Câmera 1"),
                             source=cam.get("source", 0),
                             roi_path=cam.get("roi_file", "roi_pool.yaml"),
                             on_alert=sink.handle)
    engine.start()

    print("Teclas: A=armar/desarmar  C=limpar alerta  Q=sair")
    try:
        while True:
            jpeg = engine.get_frame()
            if jpeg is not None:
                frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                cv2.imshow("PoolGuard", frame)
            sink.poll_clear()
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("a"):
                st = engine.get_status()
                engine.set_armed(not st["armed"])
                print("ARMADO:", not st["armed"])
            if key == ord("c"):
                sink.clear()
                print("[GPIO] LATCH limpo por teclado.")
    finally:
        engine.stop()
        sink.cleanup()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
