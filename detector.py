from ultralytics import YOLO

class PersonDetector:
    def __init__(self, weights='yolov8n.pt', conf=0.5, iou=0.45, device='auto'):
        self.model = YOLO(weights)
        self.conf = conf
        self.iou = iou
        self.device = device

    def detect(self, frame):
        """
        Retorna lista de dicts: {"bbox": (x1,y1,x2,y2), "conf": float, "area": int}
        Filtra só classe 'person' (id 0).
        """
        res = self.model.predict(
            frame, conf=self.conf, iou=self.iou, device=self.device, verbose=False
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
