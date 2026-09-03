FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION=1.1.5
ARG BUILD_ARCH=aarch64

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache \
        python3 \
        py3-pip \
        py3-numpy \
        py3-opencv \
    && python3 -m venv --system-site-packages /opt/gate-vision

COPY requirements.txt /tmp/requirements.txt
RUN /opt/gate-vision/bin/pip install --no-cache-dir -r /tmp/requirements.txt

COPY app /app/app
COPY references /app/references
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENV PATH="/opt/gate-vision/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
CMD ["/run.sh"]
