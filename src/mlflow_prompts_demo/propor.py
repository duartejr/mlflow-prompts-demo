"""Propõe uma promoção: escreve em producao.lock.json o que está em @staging.

Não mexe em production. O lock alterado vira um PR (workflow prompts-promote); o
merge desse PR dispara o prompts-apply, que aplica o lock no servidor de production.

Regras:
  - só entra versão que existe no Git (edição da interface ainda não trazida
    para o Git fica de fora, com aviso);
  - versão reprovada (lista "reprovadas" do lock) fica de fora: a entrada atual
    do lock é mantida até sair uma versão nova;
  - peça cujo arquivo foi apagado sai do lock.

Uso:
    uv run prompts-promote                                   # todas as peças
    uv run prompts-promote --only atendimento-formato-default
    uv run prompts-promote --resumo promocao.md              # Markdown para o PR
"""
from __future__ import annotations

import argparse
import copy

from .ambientes import servidor
from .composicao import DOMINIO
from .lock import entrada, escrever_lock, ler_lock, resumo_mudancas
from .registry import caminho_relativo, carregar, listar_arquivos, nome_do_prompt, origem, veio_do_git


def propor(apenas: str | None, arquivo_resumo: str | None) -> int:
    atual = ler_lock()
    novo = copy.deepcopy(atual)
    reprovadas = {(r["prompt"], r["git_sha"]) for r in atual["reprovadas"]}
    arquivos = {nome_do_prompt(a): caminho_relativo(a) for a in listar_arquivos()}
    avisos = []

    if apenas and apenas not in arquivos and apenas not in atual["prompts"]:
        raise SystemExit(f"Prompt desconhecido: {apenas}. Opções: {', '.join(sorted(arquivos))}")

    with servidor("staging"):
        for nome in sorted(arquivos):
            if apenas and nome != apenas:
                continue
            versao = carregar(f"prompts:/{nome}@staging", allow_missing=True)
            if versao is None:
                avisos.append(f"`{nome}`: sem @staging (rode o prompts-sync); fica como está")
                continue
            if not veio_do_git(versao):
                avisos.append(f"`{nome}`: @staging é a {origem(versao)}; traga para o Git antes (prompts-drift abre o PR)")
                continue
            sha = versao.tags["git_sha"]
            if (nome, sha) in reprovadas:
                avisos.append(f"`{nome}`: @staging ({sha}) está reprovada; production continua como está")
                continue
            novo["prompts"][nome] = entrada(sha, versao.tags["source_path"])

    for nome in list(novo["prompts"]):
        if nome not in arquivos and (not apenas or apenas == nome):
            del novo["prompts"][nome]

    escrever_lock(novo)
    resumo = resumo_mudancas(atual, novo, DOMINIO)
    if avisos:
        resumo += "\n\n### Fora desta promoção\n" + "\n".join(f"- {a}" for a in avisos)
    print(resumo)
    if arquivo_resumo:
        with open(arquivo_resumo, "w", encoding="utf-8") as f:
            f.write(resumo + "\n")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--only", default=None, help="promover só esta peça")
    p.add_argument("--resumo", default=None, help="arquivo onde gravar o resumo em Markdown")
    a = p.parse_args()
    raise SystemExit(propor(a.only, a.resumo))


if __name__ == "__main__":
    main()
