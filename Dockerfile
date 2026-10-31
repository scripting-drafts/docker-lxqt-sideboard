# Rebuilt periodically to incorporate Ubuntu security fixes. Pin a digest for
# reproducibility if you prefer manual upgrades over automatic base updates.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DISPLAY=:99 \
    HOME=/home/desktop \
    XAUTHORITY=/home/desktop/.Xauthority \
    XDG_RUNTIME_DIR=/tmp/xdg-runtime

# Keep package installation separate from account provisioning so a failure
# clearly identifies which stage needs attention.
RUN apt-get update && apt-get -y dist-upgrade && \
    apt-get install -y --no-install-recommends \
      ca-certificates curl dbus-x11 dbus-user-session \
      lxqt-core openbox pcmanfm-qt qterminal \
      novnc websockify x11vnc xvfb xauth x11-utils xinput \
      procps iproute2 tini fontconfig fonts-dejavu-core \
      xdg-utils && \
    rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 officially ships an ubuntu:ubuntu account with UID/GID 1000.
# Rename/reuse it rather than creating another account with those IDs.
# The numeric identity must remain 1000:1000 for the persistent home volume.
RUN set -eux; \
    existing_group="$(getent group 1000 | cut -d: -f1 || true)"; \
    if [ -z "$existing_group" ]; then \
      groupadd --gid 1000 desktop; \
    elif [ "$existing_group" != desktop ]; then \
      groupmod --new-name desktop "$existing_group"; \
    fi; \
    existing_user="$(getent passwd 1000 | cut -d: -f1 || true)"; \
    if [ -z "$existing_user" ]; then \
      useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash desktop; \
    elif [ "$existing_user" != desktop ]; then \
      usermod --login desktop --home /home/desktop --move-home --shell /bin/bash "$existing_user"; \
    fi; \
    test "$(id -u desktop)" -eq 1000; \
    test "$(id -g desktop)" -eq 1000; \
    mkdir -p /home/desktop/.vnc; \
    chown -R 1000:1000 /home/desktop

COPY --chmod=0755 start-desktop.sh /usr/local/bin/start-desktop
COPY --chmod=0755 scripts/check-vnc.py /usr/local/bin/check-vnc

USER desktop:desktop
WORKDIR /home/desktop

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl --fail --silent --show-error --max-time 2 http://127.0.0.1:6080/vnc.html >/dev/null && \
      /usr/local/bin/check-vnc --timeout 2 || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start-desktop"]
