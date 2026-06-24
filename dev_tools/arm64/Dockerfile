# ARM64 Dockerfile for AzurLaneAutoScript
# Build: docker build -t alas-arm64 -f dev_tools/arm64/Dockerfile .
# Run:   docker run -v ${PWD}:/app/AzurLaneAutoScript -p 25548:25548 --name alas -it --rm alas-arm64

FROM python:3.14-slim-bookworm

ENV PATH=/app/AzurLaneAutoScript/.venv/bin:${PATH}

WORKDIR /app/AzurLaneAutoScript

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        adb \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
        openssh-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory '*'

RUN uv venv --relocatable --python /usr/local/bin/python .venv && \
    uv sync --frozen --no-dev --no-install-project && \
    cp /usr/local/bin/uv .venv/bin/uv && \
    cp /usr/bin/adb .venv/bin/adb && \
    cp /usr/bin/git .venv/bin/git && \
    rm -rf /root/.cache/uv

COPY . .

CMD [".venv/bin/python", "gui.py"]
