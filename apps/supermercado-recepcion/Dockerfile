FROM python:3.12-slim

WORKDIR /srv/app

# tzdata: la app sella las horas en America/Panama (zoneinfo lo necesita
# porque la imagen slim no trae la base de zonas horarias).
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . tzdata

EXPOSE 8091

# --proxy-headers + forwarded-allow-ips: nginx corre en el host y llega por
# la red de Docker; el puerto solo se publica en loopback (compose).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
