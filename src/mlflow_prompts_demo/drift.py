"""Edições feitas fora do Git (interface do MLflow): encontra, decompõe e prepara o PR.

Olha o servidor de STAGING:
  1. prompt final editado: versão de `montado-<dominio>-<produto>` criada por uma
     pessoa -> decompõe nas peças (ver decompor.py), registra as peças em staging
     com @staging (a pessoa já pode testar) e escreve os arquivos;
  2. peça editada: última versão de uma peça criada fora do Git e diferente do
     arquivo -> escreve o texto no arquivo.
E, com dois servidores, o de PRODUCTION: versão criada lá à mão é só relatada
(production só deveria receber o que vem do lock).

Modos:
    uv run prompts-drift                      # só relata; código 1 se houver algo
    uv run prompts-drift --corrigir --corpo-pr drift.md
        # escreve arquivos + corpo do PR; o workflow abre/atualiza UM PR
        # (branch fixo prompts/edicoes-interface), então não duplica.
    uv run prompts-drift --abrir-pr
        # local: --corrigir + commit no branch fixo + push + PR (gh CLI).
        # Rodar de novo atualiza o mesmo PR.
"""
from __future__ import annotations

import argparse
import difflib
import tempfile

import mlflow

from .ambientes import dois_servidores, servidor
from .composicao import DOMINIO, PADRAO, montar, produtos
from .decompor import decompor
from .lock import ler_lock
from .montados import atualizar_montados, montado_editado, nome_montado
from .registry import (
    carregar,
    listar_arquivos,
    nome_do_prompt,
    caminho_relativo,
    origem,
    prompts_no_registro,
    raiz_repo,
    veio_do_git,
)
from .verificar import compor, ler_arquivos


def _bloco_diff(antes: str, depois: str, nome: str) -> str:
    diff = "\n".join(difflib.unified_diff(antes.splitlines(), depois.splitlines(),
                                          fromfile=f"{nome} (Git)", tofile=f"{nome} (interface)", lineterm=""))
    return f"```diff\n{diff}\n```"


def _herdeiros(secao: str) -> list[str]:
    """Produtos que hoje usam o padrão desta seção (pelos arquivos)."""
    r = compor(ler_arquivos(None))
    return sorted(p for p, f in r.fontes.items() if f.get(secao, "").endswith(f"/{PADRAO}.md"))


def decompor_montado(produto: str, versao, gravar: bool) -> str:
    nome_m = nome_montado(produto)
    autor = versao.user_id or "autor desconhecido"
    titulo = f"### Prompt final de `{produto}` editado: `{nome_m}` v{versao.version} ({autor}: \"{versao.commit_message or 'sem mensagem'}\")"
    m = montar(produto, "staging", cache_ttl=0)
    valores = {s: p.template.strip() for s, p in m.pecas.items()}
    r = decompor(m.base.template, produto, valores, versao.template)

    if r.erro or (not r.secoes and not r.novo_base):
        motivo = r.erro or "o texto é igual ao montado atual"
        if gravar:
            with servidor("staging"):
                mlflow.genai.set_prompt_version_tag(nome_m, versao.version, "decomposto", f"não: {motivo}")
        return (f"{titulo}\n\nNão foi decomposto automaticamente: {motivo}.\n"
                f"Edite os arquivos de `prompts/{DOMINIO}/` à mão (fontes atuais: "
                + ", ".join(f"`{s}` <- {f}" for s, f in m.fontes.items()) + ").")

    linhas = [titulo, "", f"Método: {r.metodo}.", "", "| Mudança | Vai para | Efeito |", "| --- | --- | --- |"]
    alteracoes: list[tuple[str, str, str]] = []  # (nome da peça, caminho, texto)
    for secao, novo in sorted(r.secoes.items()):
        peca = m.pecas[secao]
        caminho = f"prompts/{DOMINIO}/{secao}/{produto}.md"
        if peca.name.endswith(f"-{produto}"):
            efeito = f"só `{produto}`"
            onde = f"`{caminho}` (override existente)"
        else:
            outros = [p for p in _herdeiros(secao) if p != produto]
            alcance = (f"mudaria também: {', '.join(f'`{o}`' for o in outros)}" if outros
                       else "hoje nenhum outro produto herda o padrão desta seção")
            efeito = (f"só `{produto}` (novo override; o padrão não muda). Se a ideia era mudar o padrão, "
                      f"mova o texto para `prompts/{DOMINIO}/{secao}/{PADRAO}.md` ({alcance})")
            onde = f"`{caminho}` (novo)"
        linhas.append(f"| seção `{secao}` | {onde} | {efeito} |")
        alteracoes.append((f"{DOMINIO}-{secao}-{produto}", caminho, novo + "\n"))
    if r.novo_base:
        caminho = f"prompts/{DOMINIO}/base.md"
        linhas.append(f"| texto fixo do base | `{caminho}` | **todos os produtos** |")
        alteracoes.append((f"{DOMINIO}-base", caminho, r.novo_base))

    for nome, caminho, texto in alteracoes:
        destino = raiz_repo() / caminho
        antes = destino.read_text(encoding="utf-8") if destino.exists() else ""
        linhas += ["", f"`{caminho}`:", _bloco_diff(antes, texto, caminho)]
        if gravar:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(texto, encoding="utf-8")
            with servidor("staging"):
                nova = mlflow.genai.register_prompt(
                    name=nome, template=texto,
                    commit_message=f"Decomposto de {nome_m} v{versao.version}: {versao.commit_message or ''}".strip(),
                    tags={"source_path": caminho, "origem": f"decomposto de {nome_m} v{versao.version}",
                          "decomposto_de": f"{nome_m}@v{versao.version}"},
                )
                mlflow.genai.set_prompt_alias(nome, alias="staging", version=nova.version)

    if gravar:
        with servidor("staging"):
            mlflow.genai.set_prompt_version_tag(nome_m, versao.version, "decomposto", "true")
        atualizar_montados("staging")
        linhas += ["", f"As peças acima já estão em **@staging**: o `{produto}` em staging usa este texto e pode ser testado."]
        if r.novo_base:
            linhas.append("Atenção: como o base mudou, **todos os produtos** em staging já usam o base novo.")
    return "\n".join(linhas)


def pecas_divergentes(gravar: bool) -> list[str]:
    itens = []
    arquivos = {nome_do_prompt(a): caminho_relativo(a) for a in listar_arquivos()}
    with servidor("staging"):
        for nome in sorted(set(arquivos) | prompts_no_registro(DOMINIO)):
            ultima = carregar(f"prompts:/{nome}@latest", allow_missing=True)
            if ultima is None or veio_do_git(ultima):
                continue
            caminho = arquivos.get(nome) or ultima.tags.get("source_path")
            if not caminho:
                itens.append(f"### `{nome}` v{ultima.version}\n\nCriado na interface sem arquivo correspondente; "
                             "crie o arquivo em `prompts/` (o nome é o caminho com `-`).")
                continue
            destino = raiz_repo() / caminho
            antes = destino.read_text(encoding="utf-8") if destino.exists() else ""
            if antes == ultima.template:
                continue  # já está no arquivo (ex.: decomposição desta execução)
            if gravar:
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(ultima.template, encoding="utf-8")
            de_onde = (f" (vem da edição de `{ultima.tags['decomposto_de']}`)" if ultima.tags.get("decomposto_de") else "")
            itens.append(f"### `{nome}`: {origem(ultima)}{de_onde}\n\n" + _bloco_diff(antes, ultima.template, caminho))
    return itens


def edicoes_em_production() -> list[str]:
    if not dois_servidores():
        return []
    itens = []
    lock = ler_lock()
    with servidor("production"):
        for nome in sorted(lock["prompts"]):
            ultima = carregar(f"prompts:/{nome}@latest", allow_missing=True)
            if ultima is not None and not veio_do_git(ultima):
                itens.append(f"- `{nome}`: {origem(ultima)} **no servidor de production** "
                             "(edite em staging; production só recebe o que vem do lock)")
    return itens


CABECALHO = """## Edições feitas na interface do MLflow

Este PR traz para o Git o que foi editado direto na interface. As versões abaixo **já
estão em @staging** e podem ser testadas lá. Depois do merge, o `prompts-sync` as
adota (sem criar versões novas) e elas podem ser promovidas com o `prompts-promote`.

Na revisão, confira principalmente **onde** cada mudança foi parar (tabela de cada
item): override do produto, padrão da seção ou base.
"""


def drift(corrigir: bool, corpo_pr: str | None) -> int:
    itens = []
    for produto in produtos():
        versao = montado_editado(produto)
        if versao is None:
            continue
        if corrigir:
            itens.append(decompor_montado(produto, versao, gravar=True))
        else:
            itens.append(f"### `{nome_montado(produto)}` v{versao.version}: prompt final editado na interface, ainda não decomposto")

    itens += pecas_divergentes(gravar=corrigir)
    producao = edicoes_em_production()

    texto = CABECALHO + "\n" + ("\n\n".join(itens) if itens else "Nenhuma edição pendente.")
    if producao:
        texto += "\n\n## Edições direto em production\n\n" + "\n".join(producao)
    print(texto)
    if corpo_pr:
        with open(corpo_pr, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
    if producao:
        return 1
    return 1 if (itens and not corrigir) else 0


BRANCH_PR = "prompts/edicoes-interface"
TITULO_PR = "Edições feitas na interface do MLflow"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corrigir", action="store_true", help="decompõe, escreve arquivos e registra em staging")
    p.add_argument("--corpo-pr", help="arquivo onde gravar o corpo do PR (Markdown)")
    p.add_argument("--abrir-pr", action="store_true",
                   help=f"implica --corrigir; commita prompts/ no branch {BRANCH_PR}, faz push e abre/atualiza o PR (gh)")
    a = p.parse_args()

    if not a.abrir_pr:
        raise SystemExit(drift(a.corrigir, a.corpo_pr))

    from .pr_local import abrir_pr

    corpo = a.corpo_pr or tempfile.NamedTemporaryFile(prefix="drift-", suffix=".md", delete=False).name
    codigo = drift(True, corpo)
    abrir_pr(BRANCH_PR, TITULO_PR, corpo, ["prompts"], TITULO_PR)
    raise SystemExit(codigo)


if __name__ == "__main__":
    main()
