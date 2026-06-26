# docker build -t hgjazhgj/alas:latest -f deploy/docker/Dockerfile .
# docker run -v ${PWD}:/app/AzurPilot -p 25548:25548 --name AzurPilot -it --rm hgjazhgj/alas

FROM python:3.14-slim-bookworm

ARG UV_INDEX_URL=https://pypi.org/simple
ARG UV_EXTRA_INDEX_URL=
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=127.0.0.1,localhost

ENV UV_INDEX_URL=${UV_INDEX_URL}
ENV UV_EXTRA_INDEX_URL=${UV_EXTRA_INDEX_URL}
ENV http_proxy=${HTTP_PROXY}
ENV https_proxy=${HTTPS_PROXY}
ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV no_proxy=${NO_PROXY}
ENV NO_PROXY=${NO_PROXY}

WORKDIR /app/AzurPilot

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    openssh-client \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libxcb1 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN git config --system --add safe.directory /app/AzurPilot

CMD ["uv", "run", "python", "gui.py"]
