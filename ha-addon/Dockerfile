ARG BUILD_FROM
FROM ${BUILD_FROM}

# enbrighten2mqtt is installed from the repo (addon build context is this
# folder, so we pip-install from GitHub rather than COPY the parent tree).
# Override EB_REF with a tag/commit for a pinned build.
ARG EB_REF=main
ARG EB_REPO=https://github.com/youruser/enbrighten2mqtt

# cryptography has musllinux wheels for aarch64/amd64; build deps are a fallback.
RUN apk add --no-cache python3 py3-pip \
    && apk add --no-cache --virtual .build gcc musl-dev libffi-dev openssl-dev \
    && pip3 install --no-cache-dir --break-system-packages "git+${EB_REPO}@${EB_REF}" \
    && apk del .build

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
