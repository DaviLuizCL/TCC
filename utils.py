import yaml, time, numpy as np, cv2

def load_roi(path: str):
    with open(path, 'r') as f:
        data = yaml.safe_load(f)
    pts = np.array(data['polygon'], dtype=np.int32)
    return pts

def point_in_poly(pt, poly):
    # Retorna True se centro está dentro do polígono
    return cv2.pointPolygonTest(poly, pt, False) >= 0

def center_of(bbox):
    x1, y1, x2, y2 = bbox
    return (int((x1 + x2) // 2), int((y1 + y2) // 2))

class DwellTimer:
    def __init__(self, dwell_seconds: float):
        self.dwell = dwell_seconds
        self._t0 = None

    def update(self, condition: bool):
        now = time.time()
        if condition:
            if self._t0 is None:
                self._t0 = now
            return (now - self._t0) >= self.dwell
        else:
            self._t0 = None
            return False

class Cooldown:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last = 0.0

    def ready(self):
        return (time.time() - self._last) >= self.seconds

    def mark(self):
        self._last = time.time()
