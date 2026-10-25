"""Traz para o Git uma versão criada na interface do MLflow (registro -> arquivo).

Uso:
    uv run prompts-pull --only atendimento-restricoes-seguro --version 4
    uv run prompts-pull --only atendimento-restricoes-seguro --alias staging
    uv run prompts-pull --only atendimento-restricoes-seguro            # @latest

Lê do servidor de STAGING. Escreve o texto da versão no arquivo de prompts/ e para por aí:
revisar (git diff), commitar e abrir o PR são passos humanos. Depois do merge,
o prompts-sync encontra o arquivo igual à versão, ADOTA a versão (sem criar
duplicata) e o prompts-promote passa a aceitá-la.
"""
from __future__ import annotations

import argparse

import mlflow

from .ambientes import servidor
from .registry import carregar, carregar_versao, listar_arquivos, nome_do_prompt, raiz_repo, veio_do_git


def arquivo_do_prompt(nome: str, prompt):
    for arquivo in listar_arquivos():
        if nome_do_prompt(arquivo) == nome:
            return arquivo
    if prompt.tags.get("source_path"):  # peça nova (ex.: override criado por decomposição)
        return raiz_repo() / prompt.tags["source_path"]
    raise SystemExit(
        f"{nome}: não há arquivo em prompts/ para este prompt. "
        "Se ele foi criado do zero na interface, crie o arquivo no caminho certo "
        "(o nome é o caminho com '-') e rode de novo."
    )


def puxar(nome: str, versao: int | None, alias: str | None) -> None:
    with servidor("staging"):
        if versao is not None:
            prompt = carregar_versao(nome, versao)
        else:
            prompt = carregar(f"prompts:/{nome}@{alias or 'latest'}")

    arquivo = arquivo_do_prompt(nome, prompt)
    caminho = arquivo.relative_to(raiz_repo()).as_posix()

    if arquivo.exists() and arquivo.read_text(encoding="utf-8") == prompt.template:
        print(f"{caminho} já tem o texto da v{prompt.version}; nada a fazer.")
        return
    if veio_do_git(prompt):
        print(f"Aviso: v{prompt.version} já existe no Git (commit {prompt.tags['git_sha']}); "
              "isto vai reverter o arquivo para ela.")

    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(prompt.template, encoding="utf-8")
    autor = prompt.user_id or "autor desconhecido"
    print(f"{caminho} <- {nome} v{prompt.version} ({autor}: \"{prompt.commit_message or 'sem mensagem'}\")")
    print("\nPróximos passos:")
    print(f"  git diff {caminho}")
    print(f'  git commit -am "{nome}: traz v{prompt.version} editada na interface"')
    print("  PR -> revisão -> merge -> prompts-sync (adota a versão) -> prompts-promote")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--only", required=True, help="nome do prompt no registro")
    grupo = p.add_mutually_exclusive_group()
    grupo.add_argument("--version", type=int)
    grupo.add_argument("--alias")
    a = p.parse_args()
    puxar(a.only, a.version, a.alias)


if __name__ == "__main__":
    main()
