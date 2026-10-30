# syntax=docker/dockerfile:1

# The lab database: the pinned PostgreSQL image plus a one-time role script.
# Baking the script in avoids bind mounts from the repository folder.
FROM postgres:17@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3

# The server starts as postgres, so the entrypoint never needs gosu to drop
# root; removing it also removes its outdated Go runtime from the image.
RUN rm /usr/local/bin/gosu

COPY --chmod=0755 10-app-role.sh /docker-entrypoint-initdb.d/10-app-role.sh

USER postgres
