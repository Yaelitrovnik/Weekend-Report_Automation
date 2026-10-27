FROM weekend-report-runtime-base:python314

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system weekend \
    && useradd \
        --system \
        --gid weekend \
        --home /app \
        weekend

COPY app /app/app
COPY scripts /app/scripts
COPY TAG pyproject.toml /app/

# Fail offline builds using an outdated base; never install packages at runtime.
RUN python -c "import paramiko; assert paramiko.__version__ == '5.0.1'"

RUN mkdir -p /app/runs /app/data /app/config \
    && chown -R weekend:weekend /app

USER weekend

EXPOSE 8080

CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8080"]