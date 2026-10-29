"""Como o MLflow Prompt Registry trata um prompt .md com Jinja.

Registra exemplos/jinja/base.md num MLflow local (SQLite numa pasta temporária),
carrega de volta e mostra, caso a caso, o que o MLflow faz e o que muda com
variaveis.py. Não precisa de servidor.

Uso:
    uv run python exemplos/jinja/demo.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import jinja2  # noqa: E402
import mlflow  # noqa: E402
from mlflow.entities.model_registry import PromptVersion  # noqa: E402

from mlflow_prompts_demo.variaveis import eh_jinja, renderizar, variaveis  # noqa: E402

TEMPLATE = (Path(__file__).parent / "base.md").read_text(encoding="utf-8")

# Peças de seção como as de prompts/atendimento/, e regras comuns como dado.
VALORES = {
    "produto": "cartao",
    "formato": "- Responda em português, em no máximo 5 frases.",
    "restricoes": "- Não solicite número completo do cartão nem CVV.",
    "regras_comuns": [
        "Nunca peça senha.",
        "Não prometa prazo de atendimento.",
    ],
}


def titulo(texto: str) -> None:
    print(f"\n===== {texto} =====")


def main() -> None:
    # mkdtemp em vez de TemporaryDirectory: o MLflow ainda grava tags numa
    # thread depois do registro, e a pasta não pode sumir antes disso.
    pasta = tempfile.mkdtemp(prefix="mlflow-jinja-")
    mlflow.set_tracking_uri(f"sqlite:///{pasta}/mlflow.db")

    titulo("1. Registro e leitura")
    mlflow.genai.register_prompt(
        name="atendimento-base-jinja",
        template=TEMPLATE,
        commit_message="formato por canal e regras comuns",
    )
    prompt = mlflow.genai.load_prompt("prompts:/atendimento-base-jinja/1")
    print("template igual ao arquivo:", prompt.template == TEMPLATE)
    print("modo Jinja (tem {% %}):", eh_jinja(prompt.template))

    titulo("2. Renderização com todas as variáveis (canal = whatsapp)")
    print(prompt.format(**VALORES, canal="whatsapp"))

    titulo("3. Mesma chamada sem canal e sem regras_comuns: sem erro")
    incompleto = {k: v for k, v in VALORES.items() if k != "regras_comuns"}
    print(prompt.format(**incompleto))

    titulo("4. Variáveis que o template espera")
    print("prompt.variables (MLflow):", sorted(prompt.variables))
    print("variaveis() (Jinja2):     ", sorted(variaveis(prompt.template)))

    titulo("5. Com StrictUndefined, a falta vira erro")
    try:
        renderizar(prompt.template, **incompleto)
    except jinja2.UndefinedError as e:
        print("UndefinedError:", e)

    titulo("6. {% include %} no registro")
    try:
        PromptVersion("x", 1, '{% include "restricoes.md" %}').format()
    except TypeError as e:
        print("TypeError:", e)

    titulo("7. Sandbox ligado por padrão")
    try:
        PromptVersion("x", 1, "{% if 1 %}{{ ''.__class__.__mro__ }}{% endif %}").format()
    except jinja2.exceptions.SecurityError as e:
        print("SecurityError:", e)


if __name__ == "__main__":
    main()
