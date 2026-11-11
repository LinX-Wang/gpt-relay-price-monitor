FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APP_HOST=0.0.0.0 APP_PORT=8765 APP_DATA_DIR=/app/storage
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY . .
RUN mkdir -p /app/storage && chown -R app:app /app
USER app
EXPOSE 8765
CMD ["python", "app.py"]
