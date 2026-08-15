# syntax=docker/dockerfile:1
FROM postgres:17-alpine

# The W5 gate runs this image on its isolated internal network. Packaging the
# migrations avoids runtime host binds on Docker Desktop/Colima.
COPY migrations /opt/specpilot/migrations
