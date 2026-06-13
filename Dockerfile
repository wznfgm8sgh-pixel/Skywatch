# Skywatch — live ADS-B portal for a home ground station.
# Pure Python standard library, so the image is tiny and has no
# dependencies to install.
FROM python:3.12-slim

WORKDIR /app
COPY bridge.py skywatch.html ./

# Port the portal is served on (overridable with -e PORT=...).
ENV PORT=8088
EXPOSE 8088

# Basic healthcheck: the data endpoint must answer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8088')+'/data/aircraft.json', timeout=4)" || exit 1

CMD ["python3", "bridge.py"]
