FROM python:3.12-slim
WORKDIR /srv/app
COPY pyproject.toml ./
COPY app ./app
# tzdata: zoneinfo America/Panama para los timestamps de la app
RUN pip install --no-cache-dir . tzdata
EXPOSE 8092
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8092", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
