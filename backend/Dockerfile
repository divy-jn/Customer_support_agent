FROM python:3.12-slim

WORKDIR /app

COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY . .

ARG BUILD_GIT_SHA=unknown
ENV BUILD_GIT_SHA=${BUILD_GIT_SHA}
ENV PORT=8002
EXPOSE ${PORT}

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
