import cv2, yaml, argparse
import numpy as np

points = []

def mouse_cb(event, x, y, flags, param):
    global points
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append((x, y))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default='0', help='0=webcam ou caminho do vídeo')
    ap.add_argument('--out', default='roi_pool.yaml')
    args = ap.parse_args()

    cap = cv2.VideoCapture(0 if args.source == '0' else args.source)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError('Não foi possível ler do source')

    win = 'Defina ROI (clique pontos, ENTER p/ salvar, ESC p/ limpar)'
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, mouse_cb)

    while True:
        vis = frame.copy()
        for i, p in enumerate(points):
            cv2.circle(vis, p, 4, (0, 255, 0), -1)
            if i > 0:
                cv2.line(vis, points[i-1], p, (0, 255, 0), 2)
        if len(points) > 2:
            cv2.polylines(vis, [np.array(points, dtype=np.int32)], True, (0, 255, 255), 2)

        cv2.imshow(win, vis)
        key = cv2.waitKey(1) & 0xFF
        if key == 13:  # ENTER
            break
        if key == 27:  # ESC -> limpar
            points.clear()

    data = {'polygon': points}
    with open(args.out, 'w') as f:
        yaml.safe_dump(data, f)

    cap.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()
