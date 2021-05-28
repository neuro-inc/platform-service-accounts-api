FROM python:3.7.8-stretch AS installer

ARG PIP_EXTRA_INDEX_URL

# Separate step for requirements to speed up docker builds
COPY platform_service_accounts_api.egg-info/requires.txt requires.txt
RUN python -c 'from pkg_resources import Distribution, PathMetadata;\
dist = Distribution(metadata=PathMetadata(".", "."));\
print("\n".join(str(r) for r in dist.requires()));\
' > requirements.txt
RUN pip install --user -r requirements.txt

ARG DIST_FILENAME

# Install service itself
COPY dist/${DIST_FILENAME} ${DIST_FILENAME}
RUN pip install --user $DIST_FILENAME

FROM python:3.7.8-stretch as service

WORKDIR /app

COPY --from=installer /root/.local/ /root/.local/

ENV PATH=/root/.local/bin:$PATH

ENV NP_SERVICE_ACCOUNTS_API_PORT=8080
EXPOSE $NP_SERVICE_ACCOUNTS_API_PORT

CMD platform-service-accounts-api
