"""Sincroniza prompts/ (Git) com o registro de STAGING e aponta @staging.

Para cada arquivo .md, comparando com a última versão do registro (@latest):

  - não existe no registro        -> registra a v1;
  - texto igual                   -> nada a registrar ("inalterado");
      se essa versão foi criada fora do Git (interface ou decomposição) e o texto
      já está no Git (prompts-pull + PR), ela é ADOTADA: ganha as tags do Git;
  - texto diferente, e a última versão veio do Git -> registra uma nova versão;
  - texto diferente, e a última versão foi criada FORA do Git -> PULA esse
      prompt e avisa (código 1 só com --estrito). Registrar o arquivo por cima
      jogaria fora a edição feita na interface sem ninguém decidir isso. O
      prompts-drift abre um PR com ela; para jogar fora, --descartar-interface.

Prompts cujo arquivo foi apagado perdem o @staging, exceto peças novas criadas
fora do Git que ainda aguardam o PR. No fim, atualiza os montados.

Uso:
    uv run prompts-sync
    uv run prompts-sync --dry-run          # só relata (PR)
    uv run prompts-sync --descartar-interface atendimento-restricoes-seguro
"""
from __future__ import annotations

import argparse
import sys

import mlflow

from .ambientes import servidor
from .montados import atualizar_montados
from .registry import (
    caminho_relativo,
    carregar,
    git,
    listar_arquivos,
    nome_do_prompt,
    origem,
    prompts_orfaos,
    tags_do_prompt,
    ultimo_commit,
    veio_do_git,
)

ALIAS = "staging"


def sincronizar(descartar: set[str], dry_run: bool = False, estrito: bool = False) -> int:
    divergentes = []
    seco = " [dry-run]" if dry_run else ""

    with servidor("staging"):
        for arquivo in listar_arquivos():
            nome = nome_do_prompt(arquivo)
            texto = arquivo.read_text(encoding="utf-8")
            atual = carregar(f"prompts:/{nome}@latest", allow_missing=True)
            caminho = caminho_relativo(arquivo)
            sha = ultimo_commit(caminho)
            tags = tags_do_prompt(arquivo, sha)

            if atual is not None and atual.template == texto:
                versao, status = atual.version, "inalterado"
                if not veio_do_git(atual):
                    if not dry_run:
                        tags_adocao = {**tags, "origem": atual.tags.get("origem", "interface") + ", adotada no Git"}
                        for chave, valor in tags_adocao.items():
                            mlflow.genai.set_prompt_version_tag(nome, atual.version, chave, valor)
                    status = "adotada: criada fora do Git, agora igual ao Git"

            elif atual is not None and not veio_do_git(atual) and nome not in descartar:
                divergentes.append((nome, caminho, atual))
                print(f"{nome}: DIVERGENTE, {origem(atual)}; @{ALIAS} não foi alterado")
                continue

            elif dry_run:
                versao, status = (int(atual.version) + 1 if atual else 1), "nova versão"
            else:
                mensagem = git("log", "-1", "--format=%s", "--", caminho) or "registro inicial"
                nova = mlflow.genai.register_prompt(
                    name=nome, template=texto, commit_message=f"{mensagem} ({sha})", tags=tags
                )
                versao, status = nova.version, "nova versão"
                if atual is not None and nome in descartar:
                    status += f"; v{atual.version} criada fora do Git descartada"

            if not dry_run:
                mlflow.genai.set_prompt_alias(nome, alias=ALIAS, version=versao)
            print(f"{nome}: v{versao} ({status}) -> @{ALIAS}{seco}")

        for nome in prompts_orfaos():
            ultima = carregar(f"prompts:/{nome}@latest", allow_missing=True)
            if ultima is not None and not veio_do_git(ultima):
                # Peça nova criada fora do Git (ex.: override vindo de uma decomposição)
                # aguardando o PR: não é um arquivo apagado, então mantém o alias.
                print(f"{nome}: {origem(ultima)}, ainda sem arquivo no Git (aguardando PR)")
                continue
            if not dry_run:
                mlflow.genai.delete_prompt_alias(nome, alias=ALIAS)
            print(f"{nome}: arquivo removido do Git -> sem @{ALIAS}{seco}")

    if not dry_run:
        atualizar_montados("staging")

    if divergentes:
        print("\nAVISO: há versões criadas fora do Git com texto diferente do arquivo; elas ficam")
        print("em @staging. O workflow prompts-drift abre/atualiza um PR com elas. À mão:")
        for nome, caminho, atual in divergentes:
            print(f"\n  {nome} (v{atual.version})")
            print(f"    manter a edição:    uv run prompts-pull --only {nome} --version {atual.version}")
            print(f"                        git diff {caminho}   # revise, commit, PR, merge")
            print(f"    descartar a edição: uv run prompts-sync --descartar-interface {nome}")
        return 1 if estrito else 0
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--descartar-interface", nargs="*", default=[], metavar="NOME",
        help="registra o texto do Git por cima da versão criada fora do Git (que fica no histórico)",
    )
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que faria; não escreve no registro")
    parser.add_argument("--estrito", action="store_true", help="código de saída 1 se houver divergência")
    a = parser.parse_args()
    sys.exit(sincronizar(set(a.descartar_interface), a.dry_run, a.estrito))


if __name__ == "__main__":
    main()
