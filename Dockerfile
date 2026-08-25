# =============================================================================
# Portal Fiscal Nescon — Dockerfile para EasyPanel (mesmo VPS do GClick)
# - Dados (banco + XML + certificados) em /app/data -> montar UM volume la.
# - Segredos vem das ENV VARS do EasyPanel (nao da imagem).
# - libxmlsec1 e necessario para a assinatura da Ciencia 210210 (xmlsec).
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FISCAL_DATA_DIR=/app/data \
    FISCAL_XML_DIR=/app/data/XML

# Dependencias de sistema: xmlsec (assinatura XML) + openssl
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libxml2-dev libxmlsec1-dev libxmlsec1-openssl openssl \
    && rm -rf /var/lib/apt/lists/*

# Usuario nao-root
RUN groupadd --gid 1000 appgroup && useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Deps Python primeiro (cache de camada)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codigo da aplicacao (dados/segredos NAO entram na imagem — ver .dockerignore)
COPY . .

# Volume persistente (banco + XML + certificados)
RUN mkdir -p /app/data/XML /app/data/Certificados && chown -R appuser:appgroup /app

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/login').status==200 else 1)" || exit 1

EXPOSE 8000

# Servidor de producao (Flask via waitress). O worker de jobs sobe junto (thread no processo).
CMD ["waitress-serve", "--listen=0.0.0.0:8000", "app:app"]
