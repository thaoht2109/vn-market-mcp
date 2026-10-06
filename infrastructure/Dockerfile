ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

# vnstock/vnai are served from vnstock's own index, not public PyPI.
ARG PIP_EXTRA_INDEX_URL=https://vnstocks.com/api/simple

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url "${PIP_EXTRA_INDEX_URL}" -r requirements.txt

COPY . .

CMD ["python", "-m", "ops.worker"]
