# SPDX-License-Identifier: AGPL-3.0-or-later
# PoolGuard — imagem para rodar no notebook como se fosse o Raspberry Pi.
#
# Base do TCC: commit ebb1037d5b20173e894d2b7bc7649f5aa2320b86  (branch master)
#                     ("README: ajustando README")
#
# Sem a lib RPi.GPIO instalada, o alerting.py cai automaticamente no mock
# (os disparos de GPIO viram prints no log) — exatamente o que queremos para
# testar localmente. O app web e os alertas (ntfy/Telegram/...) funcionam igual.

FROM python:3.11-slim-bookworm

# Dependências de sistema do OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala as dependências primeiro (aproveita cache de camada do Docker).
# torch/torchvision CPU-only: o wheel padrão do PyPI puxa ~vários GB de CUDA da
# NVIDIA que não usamos. Instalando do índice CPU a imagem cai de ~9GB p/ ~3GB.
# (No Raspberry/ARM o torch já é CPU; este índice também tem wheels aarch64.)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Código + pesos do modelo
COPY . .

# O ultralytics precisa de um diretório de config gravável
ENV YOLO_CONFIG_DIR=/app/.ultralytics

EXPOSE 8000

# Sobe o app web (painel + vídeo ao vivo + alertas)
CMD ["python", "server.py", "--config", "config.yaml"]
