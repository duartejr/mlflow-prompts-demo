#!/usr/bin/env bash
# Demonstração ponta a ponta, local, com DOIS servidores MLflow (staging e production).
#
#   ./scripts/demo.sh            # dois servidores (portas 5001 e 5002)
#   ./scripts/demo.sh --um       # um servidor só (porta 5001), aliases staging/production
#
# Cria uma cópia do projeto em ./.demo (repositório Git próprio), sobe os servidores
# e percorre o fluxo: sync -> promoção (lock) -> apply -> nova versão -> reprovação ->
# edição do prompt FINAL na "interface" -> decomposição -> PR (merge simulado) -> adoção ->
# promoção -> edição direta em production detectada.
#
# Interface: http://127.0.0.1:5001 (staging) e http://127.0.0.1:5002 (production).
# Os servidores continuam rodando no fim; para parar: ./scripts/demo.sh --parar
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
DEMO="$RAIZ/.demo"
export MLFLOW_DISABLE_AGENT_HINT=1

parar() { pkill -f "mlflow server .*--port 500[12]" 2>/dev/null || true; }
if [[ "${1:-}" == "--parar" ]]; then parar; echo "Servidores parados."; exit 0; fi

parar
rm -rf "$DEMO" && mkdir -p "$DEMO"
cd "$RAIZ"
git ls-files --cached --others --exclude-standard -z | grep -zv '^\.demo/' | xargs -0 -I{} cp --parents {} "$DEMO"/ 2>/dev/null || \
  rsync -a --exclude .git --exclude .venv --exclude .demo --exclude '*.db' ./ "$DEMO"/
cd "$DEMO"

titulo() { printf '\n\033[1m### %s\033[0m\n' "$*"; }
commit() { git add -A && git -c user.name=Demo -c user.email=demo@example.com commit -qm "$1"; }
editar_na_interface() {  # simula alguém criando uma versão pela interface do MLflow
  uv run -q python - "$@" <<'PY'
import sys, mlflow
uri, nome, de, para, msg = sys.argv[1:6]
mlflow.set_tracking_uri(uri)
v = mlflow.genai.load_prompt(f"prompts:/{nome}@staging", cache_ttl_seconds=0)
texto = v.template
for a, b in zip(de.split("|"), para.split("|")):
    texto = texto.replace(a, b)
n = mlflow.genai.register_prompt(name=nome, template=texto.rstrip("\n"), commit_message=msg)
print(f"{nome}: v{n.version} criada 'na interface' ({msg})")
PY
}

git init -q -b main && commit "Prompts iniciais"
uv sync -q

if [[ "${1:-}" == "--um" ]]; then
  export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
  PORTAS=(5001)
else
  export MLFLOW_TRACKING_URI_STAGING=http://127.0.0.1:5001
  export MLFLOW_TRACKING_URI_PRODUCTION=http://127.0.0.1:5002
  PORTAS=(5001 5002)
fi
for p in "${PORTAS[@]}"; do
  nohup uv run mlflow server --backend-store-uri "sqlite:///$DEMO/mlflow-$p.db" --host 127.0.0.1 --port "$p" > "$DEMO/mlflow-$p.log" 2>&1 &
done
for p in "${PORTAS[@]}"; do
  until curl -sf "http://127.0.0.1:$p/health" >/dev/null; do sleep 1; done
done
STAGING_URI="${MLFLOW_TRACKING_URI_STAGING:-$MLFLOW_TRACKING_URI}"
PROD_URI="${MLFLOW_TRACKING_URI_PRODUCTION:-$MLFLOW_TRACKING_URI}"

titulo "1. PR inicial: validação offline e sync para staging"
uv run prompts-check
uv run prompts-sync

titulo "2. Promoção inicial: propor lock (vira PR), merge, apply em production"
uv run prompts-promote >/dev/null
commit "Promoção inicial"
uv run prompts-apply

titulo "3. Nova versão do formato padrão: sync, promoção, apply"
echo "- Use linguagem simples, sem jargão técnico ou jurídico." >> prompts/atendimento/formato/default.md
commit "Formato padrão: linguagem simples"
uv run prompts-sync
uv run prompts-promote | sed -n '/### Peças/,$p'
commit "Promove linguagem simples"
uv run prompts-apply

titulo "4. Versão pior chega a production e é reprovada"
echo "- Antes de responder, resuma a pergunta e liste todas as opções." >> prompts/atendimento/formato/default.md
commit "Formato padrão: resumir e listar opções"
uv run prompts-sync >/dev/null && uv run prompts-promote >/dev/null && commit "Promove resumo" && uv run prompts-apply >/dev/null
uv run prompts-reject --only atendimento-formato-default --motivo "respostas longas demais; clientes desistem"
commit "Reprova formato padrão"
uv run prompts-apply
echo "--- propor de novo: a reprovada continua em staging, mas não volta para production"
uv run prompts-promote | tail -3
git checkout -q producao.lock.json

titulo "5. Alguém edita o prompt FINAL do seguro na interface (servidor de staging)"
editar_na_interface "$STAGING_URI" montado-atendimento-seguro \
  "- Antes de responder, resuma a pergunta e liste todas as opções.|Não confirme aprovação de sinistro." \
  "- Se a pergunta for ambígua, faça uma única pergunta de esclarecimento.|Não confirme nem negue aprovação de sinistro." \
  "Pedido da operação: esclarecer ambiguidade e não negar sinistro"

titulo "6. prompts-drift (de hora em hora no CI): decompõe, põe em @staging e prepara o PR"
uv run prompts-drift --corrigir --corpo-pr "$DEMO/pr-edicoes.md" >/dev/null
cat "$DEMO/pr-edicoes.md"
echo "--- staging já usa a edição:"
uv run prompts-show --produto seguro --ambiente staging | head -4

titulo "7. Merge do PR de edições: sync adota as versões (sem duplicar)"
commit "Edições feitas na interface do MLflow"
uv run prompts-sync

titulo "8. Promoção com as edições"
uv run prompts-promote | sed -n '/### Peças/,$p'
commit "Promove edições do seguro"
uv run prompts-apply

if [[ ${#PORTAS[@]} -eq 2 ]]; then
  titulo "9. Alguém mexe direto no servidor de production: o drift acusa"
  uv run -q python -c "
import mlflow; mlflow.set_tracking_uri('$PROD_URI')
mlflow.genai.register_prompt(name='atendimento-base', template='outro {{ produto }} {{ formato }} {{ restricoes }}', commit_message='mexi direto em production')
" 2>/dev/null
  uv run prompts-drift | tail -3 || echo "(prompts-drift terminou com código 1, como esperado)"
fi

titulo "Pronto"
echo "Staging:    $STAGING_URI"
echo "Production: $PROD_URI"
echo "Repositório da demonstração: $DEMO   (git log --oneline)"
echo "Para parar os servidores: ./scripts/demo.sh --parar"
