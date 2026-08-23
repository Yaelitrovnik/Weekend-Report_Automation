FROM python:3.14-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52

ENV PYTHONDONTWRITEBYTECODE=1         PYTHONUNBUFFERED=1         PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system weekend && useradd --system --gid weekend --home /app weekend

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN mkdir -p /app/runs /app/data && chown -R weekend:weekend /app

USER weekend

EXPOSE 8080
CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8080"]
