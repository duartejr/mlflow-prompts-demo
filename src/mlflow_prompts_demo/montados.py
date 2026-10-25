"""Prompt completo de cada produto, para leitura humana (e edição por quem não é técnico).

1. Terminal: `uv run prompts-show [--produto cartao] [--ambiente staging] [--diff]`
   monta na hora, a partir dos aliases, e mostra de onde veio cada seção.

2. Interface do MLflow: os scripts registram o resultado da montagem como um prompt
   próprio, `montado-<dominio>-<produto>`, em cada servidor, com o alias do ambiente
   (@staging no servidor de staging, @production no de production). Cada versão
   leva tags `fonte_<secao>` dizendo de qual peça veio cada seção, e
   `gerado_por=prompts-ci`.

   A aplicação não carrega esses prompts. Se alguém criar uma versão do montado na
   interface (editando o texto final), o prompts-drift percebe (versão sem
   `gerado_por`) e a DECOMPÕE nas peças (ver decompor.py).
"""
from __future__ import annotations

import argparse
import difflib

import mlflow

from .ambientes import servidor
from .composicao import DOMINIO, montar, produtos
from .registry import GERADO_POR, carregar, versao_com_texto

PREFIXO = f"montado-{DOMINIO}"


def nome_montado(produto: str) -> str:
    return f"{PREFIXO}-{produto}"


def atualizar_montados(ambiente: str) -> None:
    """Registra/aponta o montado de cada produto no servidor do ambiente."""
    ativos = produtos()
    with servidor(ambiente):
        for produto in ativos:
            nome = nome_montado(produto)
            try:
                m = montar(produto, ambiente, cache_ttl=0)
            except Exception as erro:  # alias ainda não existe em alguma peça
                print(f"{nome}: não montado em @{ambiente} ({erro})")
                continue

            existente = versao_com_texto(nome, m.texto)
            if existente is None:
                tags = {"tipo": "montado", "dominio": DOMINIO, "produto": produto, "gerado_por": GERADO_POR}
                tags.update({f"fonte_{secao}": fonte for secao, fonte in m.fontes.items()})
                versao = mlflow.genai.register_prompt(
                    name=nome,
                    template=m.texto,
                    commit_message=f"Montado em @{ambiente}: " + ", ".join(m.fontes.values()),
                    tags=tags,
                ).version
                status = "nova versão"
            else:
                versao, status = existente.version, "mesmo texto"
            mlflow.genai.set_prompt_alias(nome, alias=ambiente, version=versao)
            print(f"{nome}: v{versao} ({status}) -> @{ambiente}")

        # Produto que deixou de existir no Git: tira o alias da visão também
        for prompt in mlflow.genai.search_prompts(filter_string=f"name LIKE '{PREFIXO}-%'"):
            if prompt.name.removeprefix(f"{PREFIXO}-") not in ativos:
                mlflow.genai.delete_prompt_alias(prompt.name, alias=ambiente)


def montado_editado(produto: str):
    """Última versão do montado no servidor de staging, se foi criada por uma pessoa
    (sem gerado_por) e ainda não foi decomposta. Senão, None."""
    with servidor("staging"):
        ultima = carregar(f"prompts:/{nome_montado(produto)}@latest", allow_missing=True)
    if ultima is None:
        return None
    if ultima.tags.get("gerado_por") == GERADO_POR or ultima.tags.get("decomposto"):
        return None
    return ultima


def diff_texto(produto: str) -> str:
    """O que muda no prompt completo de production para staging."""
    prod, stag = montar(produto, "production", 0), montar(produto, "staging", 0)
    linhas = [f"===== {produto}: production -> staging ====="]
    for secao in sorted(set(prod.fontes) | set(stag.fontes)):
        p, s = prod.fontes.get(secao), stag.fontes.get(secao)
        marca = "" if p == s else "   <- muda"
        linhas.append(f"  {secao:<11} production {str(p):<38} staging {s}{marca}")
    diff = "\n".join(difflib.unified_diff(
        prod.texto.splitlines(), stag.texto.splitlines(),
        fromfile=f"{produto}@production", tofile=f"{produto}@staging", lineterm="",
    ))
    linhas.append(diff or "(sem diferença no texto)")
    return "\n".join(linhas) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description="Mostra o prompt completo de um produto.")
    p.add_argument("--produto", help="omitido: todos os produtos")
    p.add_argument("--ambiente", default="production", choices=["staging", "production"])
    p.add_argument("--diff", action="store_true", help="diff entre production e staging")
    a = p.parse_args()

    for produto in [a.produto] if a.produto else produtos():
        if a.diff:
            print(diff_texto(produto))
            continue
        m = montar(produto, a.ambiente, 0)
        print(f"===== {produto} @{a.ambiente} =====")
        for secao, fonte in m.fontes.items():
            print(f"  {secao:<11} <- {fonte}")
        print("\n" + m.texto + "\n")


if __name__ == "__main__":
    main()
