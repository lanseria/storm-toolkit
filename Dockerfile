FROM m.daocloud.io/ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# --- 1. 配置 APT 清华镜像源 ---
RUN echo "\
Types: deb\n\
URIs: https://mirrors.tuna.tsinghua.edu.cn/debian/\n\
Suites: bookworm bookworm-updates bookworm-backports\n\
Components: main contrib non-free non-free-firmware\n\
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n" \
    > /etc/apt/sources.list.d/debian.sources

# --- 2. 安装系统依赖 ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- 3. 环境变量 ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/app \
    PYTHONPATH=/app/src \
    TZ=Asia/Shanghai \
    UV_TOOL_BIN_DIR=/usr/local/bin

# --- 4. 安装 Python 依赖（利用 BuildKit 缓存） ---
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN mkdir -p /app/data
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# --- 5. 持久化目录 ---
VOLUME /app/data

# --- 6. 入口 ---
ENTRYPOINT ["python", "-m", "src.storm_toolkit.main"]
CMD ["--web"]
