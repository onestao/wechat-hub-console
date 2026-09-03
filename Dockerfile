FROM python:3.12-slim

ARG OCI_REVISION=""
ARG OCI_VERSION=""

LABEL org.opencontainers.image.source="https://github.com/onestao/wechat-hub-console"
LABEL org.opencontainers.image.revision="${OCI_REVISION}"
LABEL org.opencontainers.image.version="${OCI_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WECHAT_CORE_URL=http://wechat-core:8080 \
    WECHAT_CONSOLE_RUNTIME_DIR=/data/wechat-console

WORKDIR /app

# D intentionally has no Python package dependency on Core, EFB or Agent.
# The upstream repository and LICENSE remain in this derived source tree; the
# production Console image contains only the decoupled Console package.
COPY wechat_console ./wechat_console

EXPOSE 8078
VOLUME ["/data/wechat-console"]

CMD ["python", "-m", "wechat_console.app", "--host", "0.0.0.0", "--port", "8078"]
