#!/bin/sh
# 8INF919 - TP1 : assemble le depot de l'etudiant et les elements imposes, puis
# lance le consommateur (mode "app") ou la suite de tests (mode "tests").
#
#   /student  depot de l'etudiant, monte en lecture seule
#   /imposed  elements imposes, contenus dans l'image
#   /run/tp   copie de travail, en memoire (tmpfs), jetee a la fin
# Les trois chemins se changent par TP_SRC, TP_IMPOSE et TP_RUN (essais hors Docker).
set -eu

MODE="${1:-app}"
SRC="${TP_SRC:-/student}"
IMPOSE="${TP_IMPOSE:-/imposed}"
RUN="${TP_RUN:-/run/tp}"

if [ ! -f "$SRC/src/pipeline.py" ]; then
    echo "Erreur : src/pipeline.py est introuvable dans le depot monte sur $SRC." >&2
    echo "Lancez la commande depuis la racine du depot." >&2
    exit 2
fi

# 1. copie du depot, sans dossiers volumineux ou propres a l'environnement local
tar -C "$SRC" \
    --exclude=./.git --exclude=./.venv --exclude=./venv --exclude=./.idea \
    --exclude=./.vscode --exclude=./results --exclude=./node_modules \
    --exclude=./generateur --exclude=./solution \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ipynb_checkpoints \
    -cf - . | tar -C "$RUN" -xf -

# 2. rien de ce qui modifierait le comportement de pytest ou de Python
find "$RUN" -type l -delete
rm -f "$RUN/conftest.py" "$RUN/pytest.ini" "$RUN/tox.ini" "$RUN/setup.cfg" \
      "$RUN/pyproject.toml" "$RUN/sitecustomize.py" "$RUN/usercustomize.py"

# 3. elements imposes : remplaces par ceux de l'image
rm -rf "$RUN/tests" "$RUN/generateur"
cp -R "$IMPOSE/tests" "$RUN/tests"
mkdir -p "$RUN/generateur"
cp "$IMPOSE/generateur/generateur.py" "$RUN/generateur/generateur.py"
cp "$IMPOSE/app.py" "$RUN/app.py"
mkdir -p "$RUN/src"
for f in flux.py memoire.py annexe.py; do
    cp "$IMPOSE/src/$f" "$RUN/src/$f"
done
[ -f "$RUN/src/__init__.py" ] || : > "$RUN/src/__init__.py"

cd "$RUN"
export PYTHONPATH="$RUN"

case "$MODE" in
    app)
        exec python app.py
        ;;
    tests)
        # TP_SEEDS : seeds rejouees (publique par defaut) ; TP_PYTEST_ARGS : options
        # shellcheck disable=SC2086
        exec timeout "${TP_TIMEOUT:-900}" python -m pytest -c "$IMPOSE/pytest.ini" \
            --rootdir "$RUN" ${TP_PYTEST_ARGS:-} tests/
        ;;
    *)
        echo "Mode inconnu : $MODE (attendu : app ou tests)" >&2
        exit 2
        ;;
esac
