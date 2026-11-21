FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KDONE_DATA_DIR=/app/data
WORKDIR /app
COPY upstream/KDOne /app/upstream/KDOne
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt \
    && useradd --uid 1000 --create-home homeapp \
    && mkdir /app/data && chown homeapp:homeapp /app/data
COPY home_service.py schedules.py webapp.py config_store.py ./
COPY templates ./templates
COPY static ./static
USER homeapp
EXPOSE 8088
CMD ["python", "webapp.py", "--host", "0.0.0.0", "--port", "8088"]
