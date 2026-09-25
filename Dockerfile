FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tous les modules Python du projet, plutôt qu'une liste nominative : celle-ci
# était à mettre à jour à chaque nouveau fichier, et un module oublié faisait
# planter le worker au démarrage avec un ModuleNotFoundError.
COPY *.py ./
COPY templates ./templates
COPY static ./static

# Garde-fou : vérifie à la construction de l'image que chaque module se charge,
# donc qu'aucun fichier ne manque. On importe tout sauf app.py, dont le simple
# import déclenche un rafraîchissement complet (scraping) : hors de question
# pendant un build.
RUN python -c "import importlib, pathlib; \
[importlib.import_module(p.stem) for p in sorted(pathlib.Path('.').glob('*.py')) if p.stem != 'app']; \
print('imports ok')"

RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV DATA_DIR=/app/data \
    REFRESH_HOURS=6 \
    APP_TIMEZONE=Europe/Paris \
    PORT=8090

EXPOSE 8090

# Forme shell pour que $PORT soit réellement interprété : en forme exec,
# la variable serait passée littéralement.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f "http://localhost:${PORT}/health" || exit 1

# Forme shell également : le port d'écoute suit désormais vraiment la
# variable PORT, qui était documentée mais ignorée (5000 était en dur).
CMD gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 4 app:app
