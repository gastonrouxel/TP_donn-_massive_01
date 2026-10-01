# 8INF919 - TP1 : generateur de flux.
#
# Bibliotheque standard seule : aucune dependance a installer. Le code est
# copie dans l'image ; aucun volume n'est monte.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 gen
WORKDIR /gen
COPY generateur.py ./generateur.py
RUN chmod 644 generateur.py

USER gen
EXPOSE 8000
CMD ["python", "generateur.py"]
