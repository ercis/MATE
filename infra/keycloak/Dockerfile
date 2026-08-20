# Keycloak 26 on a Debian/Ubuntu (glibc baseline x86-64-v1) base, so it runs on
# CPUs without x86-64-v2 – e.g. a VM whose virtual CPU hides ssse3/sse4/popcnt.
#
# We reuse the EXACT upstream 26.0 distribution. Keycloak's build artifacts are
# pure JVM (architecture-independent); only the official image's UBI9 glibc
# requires x86-64-v2. So we copy /opt/keycloak onto a Temurin JDK whose glibc
# targets baseline x86-64. All Keycloak config/behaviour is identical to the
# official image – only the native base differs.
FROM quay.io/keycloak/keycloak:26.0 AS upstream

FROM eclipse-temurin:21-jdk-jammy
COPY --from=upstream /opt/keycloak /opt/keycloak
# The compose healthcheck uses bash's /dev/tcp; on Ubuntu /bin/sh is dash
# (no /dev/tcp), so point sh at bash like the upstream UBI9 image does.
RUN ln -sf /bin/bash /bin/sh \
 && groupadd -g 1000 keycloak \
 && useradd -u 1000 -g 1000 -m keycloak \
 && chown -R 1000:0 /opt/keycloak \
 && chmod -R g+rwX /opt/keycloak
USER 1000
WORKDIR /opt/keycloak
EXPOSE 8080 9000
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
