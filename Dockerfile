FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/mampfplan.db

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY seed ./seed

# Die App läuft ohne Root-Rechte und muss ihre Dateien lesen können – unabhängig von den Rechten im
# Build-Verzeichnis (z. B. umask 077 auf dem Server würde sonst "Permission denied" verursachen).
RUN chmod -R a+rX /srv/app /srv/seed

# Nicht als root laufen; die Datenbank liegt auf dem Volume /data
RUN useradd --system --uid 10001 --no-create-home mampf \
    && mkdir /data && chown mampf /data
USER mampf
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

# --no-access-log: Der geheime Link-Token steht im URL-Pfad und soll nicht in Logs landen.
# --proxy-headers: korrekte Client-/Schema-Infos hinter einem Reverse Proxy.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
