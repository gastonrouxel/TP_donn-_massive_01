# 8INF919 - TP1 : environnement d'execution du consommateur.
#
# L'image contient Python 3.14, les bibliotheques aux versions exactes de
# requirements.txt et les elements imposes (app.py, src/flux.py, src/memoire.py,
# src/annexe.py, tests/ et le generateur, rejoue par la suite de tests). Le depot de
# l'etudiant n'y est jamais copie : il est monte en lecture seule a
# l'execution (voir docker-compose.yml) puis recombine avec les elements
# imposes par docker/entrypoint.sh.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 tp

WORKDIR /imposed

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Elements imposes : appartiennent a root, lisibles mais non modifiables.
COPY app.py ./app.py
COPY src/flux.py ./src/flux.py
COPY src/memoire.py ./src/memoire.py
COPY src/annexe.py ./src/annexe.py
COPY generateur/generateur.py ./generateur/generateur.py
COPY tests/ ./tests/
COPY docker/pytest.ini ./pytest.ini
COPY docker/entrypoint.sh /usr/local/bin/tp-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/tp-entrypoint \
    && chmod 755 /usr/local/bin/tp-entrypoint \
    && find /imposed -type d -exec chmod 755 {} + \
    && find /imposed -type f -exec chmod 644 {} +

USER tp
ENTRYPOINT ["tp-entrypoint"]
CMD ["app"]
