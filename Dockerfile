FROM python:3.8.10-slim-buster AS installer

ENV PATH=/root/.local/bin:$PATH

# Copy to tmp folder to don't pollute home dir
RUN mkdir -p /tmp/dist
COPY dist /tmp/dist

RUN ls /tmp/dist
RUN pip install --user --find-links /tmp/dist platform-service-accounts-api

FROM python:3.8.10-slim-buster as service

LABEL org.opencontainers.image.source = "https://github.com/neuro-inc/platform-service-accounts-api"

WORKDIR /app

COPY --from=installer /root/.local/ /root/.local/
COPY alembic.ini alembic.ini
COPY alembic alembic

ENV PATH=/root/.local/bin:$PATH

ENV NP_SERVICE_ACCOUNTS_API_PORT=8080
EXPOSE $NP_SERVICE_ACCOUNTS_API_PORT

CMD platform-service-accounts-api
