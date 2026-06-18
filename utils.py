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
    """Mede há quanto tempo a condição está ativa, tolerando falhas breves.

    Na CPU o detector "pisca" (perde frames isolados). Sem tolerância, qualquer
    frame sem detecção zeraria a contagem e o alerta nunca dispararia no tempo.
    Por isso só reiniciamos se a condição ficar ausente por mais que `grace`.

    `update()` retorna os segundos decorridos (0.0 se inativo) — quem decide o
    limiar é quem chama, podendo usar limiares diferentes por situação.
    """

    def __init__(self, grace_seconds: float = 0.6):
        self.grace = grace_seconds
        self._t0 = None
        self._last_true = 0.0

    def update(self, condition: bool) -> float:
        now = time.time()
        if condition:
            # reinicia só se voltou depois de uma ausência longa (> grace)
            if self._t0 is None or (now - self._last_true) > self.grace:
                self._t0 = now
            self._last_true = now
        else:
            if self._t0 is not None and (now - self._last_true) > self.grace:
                self._t0 = None
        return (now - self._t0) if self._t0 is not None else 0.0

class Cooldown:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last = 0.0

    def ready(self):
        return (time.time() - self._last) >= self.seconds

    def mark(self):
        self._last = time.time()
