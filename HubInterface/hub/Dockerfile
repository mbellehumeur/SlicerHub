FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first for better build caching.
# Use Azure requirements (no editable ../python_client pin).
COPY requirements-azure.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Copy Slicer hub sources (include vendored python_client when present in build context).
COPY . /app
# Monorepo Docker builds may COPY python_client separately; zip deploy vendors it.

# Startup script for Azure Linux Web App container.
RUN chmod +x /app/start-azure.sh

# Default local container port (Azure overrides with PORT env var).
EXPOSE 8000

CMD ["/app/start-azure.sh"]
