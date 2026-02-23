# Target ARM64 — matches CI.Standard.A1.Flex shape on OCI free tier.
# Build: docker buildx build --platform linux/arm64 -t <image> .
FROM --platform=linux/arm64 python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server.py .

EXPOSE 8000

CMD ["uvicorn", "mcp_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
