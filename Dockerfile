FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
# vnstock/vnai are served from vnstock's own index, not public PyPI.
RUN pip install --no-cache-dir --extra-index-url https://vnstocks.com/api/simple -r requirements.txt

COPY . .

CMD ["python", "-m", "ops.worker"]
