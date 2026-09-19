FROM streamkeeper:0.1.0

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

COPY legacy/convert-mkv-for-apple.reference.sh /usr/local/bin/convert-mkv-reference
COPY tests/parity/stat /usr/local/bin/stat
RUN chmod 0555 /usr/local/bin/convert-mkv-reference /usr/local/bin/stat

USER plexconvert
ENTRYPOINT ["/usr/local/bin/convert-mkv-reference"]
