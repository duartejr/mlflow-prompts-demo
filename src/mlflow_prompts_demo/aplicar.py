"""Aplica producao.lock.json no servidor de PRODUCTION e aponta @production.

Para cada peça do lock, pega o texto no Git (commit + caminho) e procura no
servidor de production uma versão com exatamente esse texto. Se não houver,
registra uma (com as tags do Git). Assim o mesmo lock funciona com um servidor
só (reaproveita a versão de staging) ou com dois (copia a versão para o de
production; o número pode ser outro).

Também: tira @production de peças que saíram do lock, marca as versões
reprovadas, atualiza os montados e confere que o prompt completo de cada produto
em production é igual ao que o lock descreve.

Uso:
    uv run prompts-apply
"""
from __future__ import annotations

import argparse

import mlflow

from .ambientes import dois_servidores, servidor
from .composicao import DOMINIO, montar
from .lock import arquivos_do_lock, ler_lock, validar_lock
from .montados import atualizar_montados
from .registry import (
    carregar,
    git,
    origem,
    prompts_orfaos,
    tags_de_caminho,
    texto_no_git,
    veio_do_git,
    versao_com_texto,
)

ALIAS = "production"


def aplicar() -> int:
    lock = ler_lock()
    erros = validar_lock(lock)
    if erros:
        print("\n".join(erros))
        return 1
    if not lock["prompts"]:
        print("Lock vazio: nada a aplicar.")
        return 0

    print(f"Servidor de production: {'separado' if dois_servidores() else 'o mesmo de staging'}")
    with servidor("production"):
        for nome, e in sorted(lock["prompts"].items()):
            texto = texto_no_git(e["git_sha"], e["source_path"])
            versao = versao_com_texto(nome, texto)
            if versao is None:
                mensagem = git("log", "-1", "--format=%s", e["git_sha"]) or "promoção"
                versao = mlflow.genai.register_prompt(
                    name=nome, template=texto, commit_message=f"{mensagem} ({e['git_sha']})",
                    tags=tags_de_caminho(e["source_path"], e["git_sha"]),
                )
                status = "registrada neste servidor"
            elif not veio_do_git(versao):
                for chave, valor in tags_de_caminho(e["source_path"], e["git_sha"]).items():
                    mlflow.genai.set_prompt_version_tag(nome, versao.version, chave, valor)
                status = "existente, sem tags do Git: adotada"
            else:
                status = "existente"
            mlflow.genai.set_prompt_alias(nome, alias=ALIAS, version=versao.version)
            print(f"{nome}: v{versao.version} ({e['git_sha']}, {status}) -> @{ALIAS}")

            ultima = carregar(f"prompts:/{nome}@latest")
            if dois_servidores() and ultima.template != texto and not veio_do_git(ultima):
                print(f"  ATENÇÃO: {nome} tem uma {origem(ultima)} no servidor de production")

        for nome in prompts_orfaos(set(lock["prompts"])):
            mlflow.genai.delete_prompt_alias(nome, alias=ALIAS)
            print(f"{nome}: fora do lock -> sem @{ALIAS}")

        for r in lock["reprovadas"]:
            texto = texto_no_git(r["git_sha"], r["source_path"])
            versao = versao_com_texto(r["prompt"], texto) if texto is not None else None
            if versao is not None:
                mlflow.genai.set_prompt_version_tag(r["prompt"], versao.version, "reprovada", "true")
                mlflow.genai.set_prompt_version_tag(r["prompt"], versao.version, "motivo", r.get("motivo", ""))

    atualizar_montados(ALIAS)

    # Conferência: o que a aplicação vai montar em production == o que o lock descreve
    from .verificar import compor

    esperado = compor(arquivos_do_lock(lock, DOMINIO))
    divergencias = [p for p, t in esperado.textos.items() if montar(p, ALIAS, cache_ttl=0).texto != t]
    if divergencias:
        print(f"ERRO: prompt completo em production difere do lock para: {', '.join(divergencias)}")
        return 1
    print(f"Conferido: production monta exatamente o que o lock descreve ({', '.join(esperado.textos)}).")
    return 0


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    raise SystemExit(aplicar())


if __name__ == "__main__":
    main()
