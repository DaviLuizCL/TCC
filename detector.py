import os

from ultralytics import YOLO


class PersonDetector:
    """Detector de pessoas (classe 'person', id 0) do PoolGuard.

    Por padrão usa um modelo NCNN exportado (yolov8n_ncnn_model/), que no
    Raspberry Pi roda ~2-3x mais rápido e com bem menos RAM que o .pt PyTorch.
    Se o diretório NCNN não existir, cai automaticamente no .pt para não quebrar.
    `imgsz` baixo (ex.: 320) corta o custo da inferência ~quadraticamente.
    """

    def __init__(self, weights='yolov8n_ncnn_model', conf=0.5, iou=0.45,
                 device='cpu', imgsz=320):
        w = weights
        # Fallback: pediram NCNN mas o export ainda não foi feito -> usa o .pt.
        if str(w).endswith('_ncnn_model') and not os.path.isdir(w):
            fallback = 'yolov8n.pt'
            print(f"[DETECTOR] '{w}' não encontrado; usando '{fallback}'. "
                  f"Rode 'make export' p/ gerar o NCNN e acelerar no Pi.")
            w = fallback
        self.model = YOLO(w, task='detect')
        self.conf = conf
        self.iou = iou
        self.device = device
        self.imgsz = int(imgsz)

    def detect(self, frame):
        """
        Retorna lista de dicts: {"bbox": (x1,y1,x2,y2), "conf": float, "area": int}
        Filtra só classe 'person' (id 0) já na inferência (NMS mais barato).
        """
        res = self.model.predict(
            frame, conf=self.conf, iou=self.iou, device=self.device,
            imgsz=self.imgsz, classes=[0], verbose=False,
        )
        out = []
        for r in res:
            boxes = r.boxes
            if boxes is None:
                continue
            for b in boxes:
                cls_id = int(b.cls.item())
                if cls_id != 0:
                    continue
                x1, y1, x2, y2 = map(int, b.xyxy.cpu().numpy().flatten())
                area = int((x2 - x1) * (y2 - y1))
                conf = float(b.conf.item())
                out.append({
                    'bbox': (x1, y1, x2, y2),
                    'conf': conf,
                    'area': area,
                })
        return out
