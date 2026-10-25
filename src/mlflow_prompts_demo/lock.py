"""producao.lock.json: o que está (ou deve estar) em production, versionado no Git.

    {
      "prompts": {
        "atendimento-base": {
          "git_sha": "3f2a9c1",
          "source_path": "prompts/atendimento/base.md",
          "sha256": "…"            # do texto, para conferir integridade
        },
        …
      },
      "reprovadas": [
        {"prompt": "atendimento-formato-default", "git_sha": "90094fa",
         "source_path": "prompts/atendimento/formato/default.md",
         "motivo": "…", "por": "duartejr", "em": "2026-10-25"}
      ]
    }

Uma versão é identificada por commit + caminho, não pelo número do MLflow: assim o
mesmo lock vale para um servidor só ou para servidores separados de staging e
production (onde os números de versão não batem).

Fluxo: prompts-promote propõe um lock novo (PR) -> merge -> prompts-apply aplica
no servidor de production. prompts-reject troca a entrada e registra a reprovação.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import PurePosixPath

from .registry import git, raiz_repo, texto_no_git

ARQUIVO = "producao.lock.json"
VAZIO = {"prompts": {}, "reprovadas": []}


def caminho_lock():
    return raiz_repo() / ARQUIVO


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def ler_lock(ref: str | None = None) -> dict:
    if ref is None:
        caminho = caminho_lock()
        bruto = caminho.read_text(encoding="utf-8") if caminho.exists() else None
    else:
        bruto = git("show", f"{ref}:{ARQUIVO}", strip=False)
    dados = json.loads(bruto) if bruto else {}
    return {"prompts": dict(dados.get("prompts", {})), "reprovadas": list(dados.get("reprovadas", []))}


def escrever_lock(lock: dict) -> None:
    dados = {
        "prompts": {k: lock["prompts"][k] for k in sorted(lock["prompts"])},
        "reprovadas": lock["reprovadas"],
    }
    caminho_lock().write_text(json.dumps(dados, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def entrada(git_sha: str, source_path: str) -> dict:
    texto = texto_no_git(git_sha, source_path)
    if texto is None:
        raise SystemExit(f"{source_path} não existe no commit {git_sha}")
    return {"git_sha": git_sha, "source_path": source_path, "sha256": sha256(texto)}


def validar_lock(lock: dict) -> list[str]:
    from .registry import nome_de_caminho

    erros = []
    for nome, e in lock["prompts"].items():
        texto = texto_no_git(e.get("git_sha", ""), e.get("source_path", ""))
        if texto is None:
            erros.append(f"lock: {nome}: {e.get('source_path')} não existe no commit {e.get('git_sha')} (histórico completo? fetch-depth: 0)")
        elif sha256(texto) != e.get("sha256"):
            erros.append(f"lock: {nome}: sha256 não confere com o texto do commit {e['git_sha']}")
        if nome_de_caminho(e.get("source_path", "")) != nome:
            erros.append(f"lock: {nome}: source_path {e.get('source_path')} não corresponde ao nome")
    return erros


def arquivos_do_lock(lock: dict, dominio: str) -> dict[PurePosixPath, str]:
    """Textos das peças do lock no formato que verificar.compor usa (relativo a prompts/<dominio>/)."""
    arquivos = {}
    for e in lock["prompts"].values():
        partes = PurePosixPath(e["source_path"]).parts
        if len(partes) > 2 and partes[1] == dominio:
            arquivos[PurePosixPath(*partes[2:])] = texto_no_git(e["git_sha"], e["source_path"]) or ""
    return arquivos


def resumo_mudancas(antigo: dict, novo: dict, dominio: str, markdown: bool = True) -> str:
    """Peças que mudam e diff do prompt completo de cada produto (antigo -> novo)."""
    from .verificar import compor

    linhas = []
    nomes = sorted(set(antigo["prompts"]) | set(novo["prompts"]))
    mudancas = [n for n in nomes if antigo["prompts"].get(n) != novo["prompts"].get(n)]
    if not mudancas:
        return "Nada muda em production."

    linhas += ["### Peças", "", "| Peça | Hoje | Proposto | Commit |", "| --- | --- | --- | --- |"]
    for n in mudancas:
        a, b = antigo["prompts"].get(n), novo["prompts"].get(n)
        msg = git("log", "-1", "--format=%s", b["git_sha"]) if b else "removida (arquivo apagado)"
        linhas.append(f"| `{n}` | {a['git_sha'] if a else '(nova)'} | {b['git_sha'] if b else '(sai)'} | {msg} |")

    ra, rb = compor(arquivos_do_lock(antigo, dominio)), compor(arquivos_do_lock(novo, dominio))
    linhas += ["", "### Prompt completo por produto"]
    for produto in sorted(set(ra.textos) | set(rb.textos)):
        ta, tb = ra.textos.get(produto, ""), rb.textos.get(produto, "")
        if ta == tb:
            linhas.append(f"- `{produto}`: sem mudança")
            continue
        diff = "\n".join(difflib.unified_diff(
            ta.splitlines(), tb.splitlines(), fromfile=f"{produto}@production", tofile=f"{produto}@proposto", lineterm=""
        ))
        linhas += [f"#### {produto}", "```diff" if markdown else "", diff, "```" if markdown else ""]
    if rb.erros:
        linhas += ["", "### Erros na composição proposta"] + [f"- {e}" for e in rb.erros]
    return "\n".join(linhas)
