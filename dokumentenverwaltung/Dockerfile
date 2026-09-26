# Multi-Arch-Basis (amd64 und arm64) – Home Assistant wählt die passende Architektur
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
FROM ${BUILD_FROM}

# Texterkennung: Tesseract mit deutschem und englischem Sprachpaket (Open Source, offline)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng antiword \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt waitress pillow-heif

COPY dms ./dms
COPY run.py .
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
