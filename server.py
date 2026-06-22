# SPDX-License-Identifier: AGPL-3.0-or-later
"""server.py — app web do PoolGuard (multi-câmera + RTSP).

Sobe um DetectionEngine por câmera (config `cameras:`), compartilhando um único
AlertSink (GPIO + notificações). Serve o painel, o vídeo ao vivo de cada câmera,
e as APIs de controle/configuração/ROI. Protegido por Basic Auth (config.yaml > web).

Uso:  python server.py --config config.yaml      (ou: make server)
"""

import os
import re
import time
import secrets
import argparse
import threading
from contextlib import asynccontextmanager

import yaml
import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import StreamingResponse, FileResponse, Response

from engine import DetectionEngine, AlertSink
from discovery import list_local_cameras, discover_network

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

EDITABLE = {
    "video": ["backend", "resize_width", "infer_every", "stream_fps"],
    "model": ["weights", "conf", "iou", "device", "imgsz"],
    "alarm": ["enabled_start", "dwell_seconds", "dwell_child_alone_seconds",
              "dwell_grace_seconds", "min_box_area", "cooldown_seconds"],
}


def load_cfg(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def save_cfg(path, cfg):
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)


def norm_source(src):
    """Normaliza fontes locais p/ comparação: 0 e '/dev/video0' -> '/dev/video0'.

    Fontes não-locais (rtsp://, arquivo) voltam como string crua."""
    s = str(src).strip()
    m = re.match(r"/dev/video(\d+)$", s)
    if m:
        return "/dev/video" + m.group(1)
    if s.isdigit():
        return "/dev/video" + s
    return s


def build_cameras(cfg):
    """Lista de câmeras a partir do config; cai pro modo legado (video.source) se vazio."""
    cams = cfg.get("cameras")
    if not cams:
        v = cfg.get("video", {}) or {}
        cams = [{"id": "cam1", "name": "Câmera 1", "source": v.get("source", 0),
                 "roi_file": cfg.get("roi", {}).get("file", "roi_pool.yaml")}]
    max_cams = int((cfg.get("product", {}) or {}).get("max_cameras", len(cams)))
    return cams[:max_cams]


def create_app(config_path):
    cfg = load_cfg(config_path)

    sink = AlertSink(cfg)
    cams = build_cameras(cfg)
    engines = {}   # id -> DetectionEngine
    for cam in cams:
        engines[cam["id"]] = DetectionEngine(
            cfg, name=cam.get("name", cam["id"]), source=cam.get("source", 0),
            roi_path=cam.get("roi_file", f"roi_{cam['id']}.yaml"),
            on_alert=sink.notify_alert, on_alarm=sink.set_alarm,
        )

    web = cfg.get("web", {}) or {}
    WEB_USER, WEB_PASS = str(web.get("username", "admin")), str(web.get("password", "poolguard"))
    security = HTTPBasic()

    def auth(creds: HTTPBasicCredentials = Depends(security)):
        ok = (secrets.compare_digest(creds.username, WEB_USER)
              and secrets.compare_digest(creds.password, WEB_PASS))
        if not ok:
            raise HTTPException(401, "Não autorizado", headers={"WWW-Authenticate": "Basic"})
        return creds.username

    _stop_clear = threading.Event()

    def _clear_loop():
        while not _stop_clear.is_set():
            sink.poll_clear()
            time.sleep(0.05)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for e in engines.values():
            e.start()
        threading.Thread(target=_clear_loop, daemon=True).start()
        print(f"[SERVER] Câmeras: {[e.name for e in engines.values()]} | Canais: {sink.channel_names() or '(nenhum)'}")
        yield
        _stop_clear.set()
        for e in engines.values():
            e.stop()
        sink.cleanup()

    app = FastAPI(title="PoolGuard", lifespan=lifespan)

    def _engine(cam_id):
        e = engines.get(cam_id)
        if e is None:
            raise HTTPException(404, f"Câmera '{cam_id}' não existe")
        return e

    # ---------- saúde ----------
    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "channels": sink.channel_names(),
                "cameras": [e.get_status() for e in engines.values()]}

    @app.get("/")
    def index(_: str = Depends(auth)):
        return FileResponse(os.path.join(STATIC, "index.html"))

    @app.get("/api/cameras")
    def list_cameras(_: str = Depends(auth)):
        return [{"id": cid, "name": e.name} for cid, e in engines.items()]

    # ---------- descoberta de câmeras ----------
    @app.get("/api/discover/local")
    def discover_local(_: str = Depends(auth)):
        """Câmeras locais (USB/CSI), marcando as que já estão configuradas/em uso."""
        used = {norm_source(c.get("source")): c for c in cams}
        found = list_local_cameras()
        for f in found:
            c = used.get(norm_source(f["source"]))
            f["in_use_by"] = c.get("name", c.get("id")) if c else None
            f["in_use_id"] = c.get("id") if c else None
        return {"cameras": found}

    @app.get("/api/discover/network")
    def discover_net(timeout: float = 3.0, _: str = Depends(auth)):
        """Câmeras IP na rede (ONVIF WS-Discovery + scan da porta RTSP 554)."""
        return {"cameras": discover_network(timeout=max(0.5, min(timeout, 10.0)))}

    # ---------- vídeo ----------
    @app.get("/stream/{cam_id}")
    def stream(cam_id: str, _: str = Depends(auth)):
        e = _engine(cam_id)

        def gen():
            while True:
                f = e.get_frame()
                if f is None:
                    time.sleep(0.05)
                    continue
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + f + b"\r\n"
                time.sleep(0.04)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/snapshot/{cam_id}")
    def snapshot(cam_id: str, _: str = Depends(auth)):
        f = _engine(cam_id).get_raw()
        if f is None:
            raise HTTPException(503, "Sem frame ainda")
        return Response(content=f, media_type="image/jpeg")

    # ---------- status & controle ----------
    @app.get("/api/status")
    def status(_: str = Depends(auth)):
        return {"cameras": {cid: e.get_status() for cid, e in engines.items()}}

    @app.post("/api/arm")
    def arm(payload: dict = Body(...), _: str = Depends(auth)):
        armed = bool(payload.get("armed", True))
        cam_id = payload.get("cam_id")
        targets = [engines[cam_id]] if cam_id in engines else engines.values()
        for e in targets:
            e.set_armed(armed)
        return {"status": "ok", "armed": armed}

    @app.post("/api/clear")
    def clear(_: str = Depends(auth)):
        sink.clear()
        return {"status": "ok"}

    @app.post("/api/main_camera")
    def set_main_camera(payload: dict = Body(...), _: str = Depends(auth)):
        """Define qual câmera é a 'principal' (vídeo + ROI no painel) e persiste."""
        cam_id = payload.get("cam_id")
        if cam_id not in engines:
            raise HTTPException(404, f"Câmera '{cam_id}' não existe")
        cur = load_cfg(config_path)
        cur.setdefault("product", {})["main_camera"] = cam_id
        save_cfg(config_path, cur)
        return {"status": "ok", "main_camera": cam_id}

    # ---------- configuração ----------
    @app.get("/api/config")
    def get_config(_: str = Depends(auth)):
        return load_cfg(config_path)

    @app.post("/api/config")
    def set_config(new_cfg: dict = Body(...), _: str = Depends(auth)):
        cur = load_cfg(config_path)
        for section, keys in EDITABLE.items():
            inc = new_cfg.get(section, {}) or {}
            cur.setdefault(section, {})
            for k in keys:
                if k in inc:
                    cur[section][k] = inc[k]
        for whole in ("notify", "classify", "cameras", "motion"):
            if whole in new_cfg:
                cur[whole] = new_cfg[whole]
        if "product" in new_cfg:  # merge p/ não apagar main_camera num save parcial
            cur.setdefault("product", {}).update(new_cfg["product"] or {})
        save_cfg(config_path, cur)
        # aplica em tempo real ao que já existe; câmeras adicionadas/removidas pedem restart
        for e in engines.values():
            e.apply_config(cur)
        sink.reconfigure(cur)
        restart = "cameras" in new_cfg and \
            [c.get("id") for c in (new_cfg.get("cameras") or [])] != list(engines.keys())
        return {"status": "ok", "restart_needed": restart}

    # ---------- ROI (por câmera) ----------
    @app.get("/api/roi/{cam_id}")
    def get_roi(cam_id: str, _: str = Depends(auth)):
        e = _engine(cam_id)
        if not os.path.exists(e.roi_path):
            return {"polygon": []}
        with open(e.roi_path, "r") as f:
            return yaml.safe_load(f) or {"polygon": []}

    @app.post("/api/roi/{cam_id}")
    def set_roi(cam_id: str, payload: dict = Body(...), _: str = Depends(auth)):
        e = _engine(cam_id)
        pts = payload.get("polygon", [])
        if not isinstance(pts, list) or len(pts) < 3:
            raise HTTPException(400, "ROI precisa de ao menos 3 pontos")
        pts = [[int(p[0]), int(p[1])] for p in pts]
        with open(e.roi_path, "w") as f:
            yaml.safe_dump({"polygon": pts}, f)
        e.set_roi(pts)
        return {"status": "ok", "pontos": len(pts)}

    # ---------- gatilho externo ----------
    @app.post("/event")
    def event(payload: dict = Body(...), _: str = Depends(auth)):
        import base64
        snap = base64.b64decode(payload["snapshot_b64"]) if payload.get("snapshot_b64") else None
        res = sink.notifier.dispatch(payload.get("title"), payload.get("message"), snapshot=snap)
        return {"status": "ok", "dispatched": res}

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    web = cfg.get("web", {}) or {}
    host = args.host or web.get("host", "0.0.0.0")
    port = args.port or int(web.get("port", 8000))
    uvicorn.run(create_app(args.config), host=host, port=port)


if __name__ == "__main__":
    main()
