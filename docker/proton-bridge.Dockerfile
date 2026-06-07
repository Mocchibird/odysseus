ARG PROTON_BRIDGE_BASE=docker.io/shenxn/protonmail-bridge:latest
FROM ${PROTON_BRIDGE_BASE}

# Proton Bridge self-updates into /root/.local/share/protonmail. Newer Bridge
# launchers depend on libfido2.so.1, but the community Debian image trims
# dependencies aggressively and older tags do not include it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfido2-1 \
    && rm -rf /var/lib/apt/lists/*
