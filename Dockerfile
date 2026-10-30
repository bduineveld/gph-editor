# The online version of the editor (gph-editor.madebybart.nl): `gph serve --public`.
# Visitors upload their own GPH files; nothing is read from or written to this container's disk.
# Optional: mount the unit's GraphicBase.RNEG.gz somewhere and set GPH_SCREENS to its path
# to get the Screens view (that file is PSA/Magneti Marelli content and is not in the image).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -r -u 10001 gph

ENV PORT=8765 GPH_PUBLIC=1 PYTHONUNBUFFERED=1
EXPOSE 8765
USER gph
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/settings', timeout=4)"
CMD ["gph", "serve", "--host", "0.0.0.0", "--no-browser", "--public"]
