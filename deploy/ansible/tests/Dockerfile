# Disposable first-install check target. It has no Tracewake users, services,
# checkout, model credentials, or Docker Sandboxes installation.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-apt sudo ansible-core \
    && apt-get clean
CMD ["sleep", "infinity"]
