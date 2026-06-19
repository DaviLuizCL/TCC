PYTHON := .venv/bin/python3

.PHONY: start roi server export

start:
	$(PYTHON) main.py

roi:
	$(PYTHON) roi_setup.py --source 2

server:
	$(PYTHON) server.py --config config.yaml

# Exporta o YOLOv8n para NCNN (yolov8n_ncnn_model/), formato otimizado p/ ARM.
# Roda uma vez; no Raspberry Pi acelera a inferência ~2-3x e usa menos RAM.
export:
	$(PYTHON) -c "from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='ncnn', imgsz=320)"
