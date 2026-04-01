ARG BUILD_FROM
FROM ${BUILD_FROM}

# Python + зависимости
RUN apk add --no-cache python3 py3-pip && \
    pip3 install --no-cache-dir --break-system-packages pyserial requests

# Копируем файлы
COPY rootfs/ /
COPY gate_daemon.py /usr/local/bin/gate_daemon.py

# Права на запуск
RUN chmod a+x /usr/local/bin/run.sh /usr/local/bin/gate_daemon.py

CMD ["/usr/local/bin/run.sh"]
