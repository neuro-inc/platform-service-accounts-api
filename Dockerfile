ARG PY_VERSION=3.13.2

FROM python:${PY_VERSION}-slim-bookworm AS builder

ENV PATH=/root/.local/bin:$PATH

WORKDIR /tmp
COPY requirements.txt /tmp/

RUN pip install --user --no-cache-dir -r requirements.txt

COPY dist /tmp/dist/
RUN pip install --user --no-cache-dir --find-links /tmp/dist platform-service-accounts-api && \
    rm -rf /tmp/dist

FROM python:${PY_VERSION}-slim-bookworm AS runtime

LABEL org.opencontainers.image.source = "https://github.com/neuro-inc/platform-service-accounts-api"

ARG SERVICE_NAME="platform-service-accounts-api"
ARG SERVICE_UID=1001
ARG SERVICE_GID=1001

RUN addgroup --gid $SERVICE_GID $SERVICE_NAME && \
    adduser --uid $SERVICE_UID --gid $SERVICE_GID \
    --home /home/$SERVICE_NAME --shell /bin/false \
    --disabled-password --gecos "" $SERVICE_NAME && \
    mkdir -p /var/log/$SERVICE_NAME && \
    chown $SERVICE_NAME:$SERVICE_NAME /var/log/$SERVICE_NAME

COPY --from=builder --chown=$SERVICE_NAME:$SERVICE_GID /root/.local /home/$SERVICE_NAME/.local

WORKDIR /app
COPY alembic.ini alembic.ini
COPY alembic alembic

USER $SERVICE_NAME
ENV PATH=/home/$SERVICE_NAME/.local/bin:$PATH
ENV NP_SERVICE_ACCOUNTS_API_PORT=8080
EXPOSE $NP_SERVICE_ACCOUNTS_API_PORT

ENTRYPOINT [ "platform-service-accounts-api" ]
