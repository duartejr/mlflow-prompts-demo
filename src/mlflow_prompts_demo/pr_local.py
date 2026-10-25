"""Abre (ou atualiza) um PR a partir da máquina local, com git + GitHub CLI (gh).

Faz o mesmo que o passo peter-evans/create-pull-request dos workflows:
  - usa um branch FIXO (ex.: prompts/edicoes-interface): rodar de novo atualiza o
    mesmo PR em vez de abrir outro;
  - commita só os caminhos indicados (ex.: prompts/), sem levar outras mudanças
    do seu working tree;
  - deixa você de volta no branch onde estava.

O branch fixo é regenerado a cada execução (push --force), como no workflow.
Commits feitos à mão nele durante a revisão são sobrescritos na próxima execução.
"""
from __future__ import annotations

import shutil
import subprocess

from .registry import raiz_repo


class ErroPR(SystemExit):
    pass


def _rodar(*args: str, checar: bool = True) -> str:
    r = subprocess.run(args, cwd=raiz_repo(), capture_output=True, text=True)
    if checar and r.returncode != 0:
        raise ErroPR(f"Falhou: {' '.join(args)}\n{(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def _pre_requisitos() -> None:
    if shutil.which("gh") is None:
        raise ErroPR("GitHub CLI (gh) não encontrado. Instale em https://cli.github.com e rode `gh auth login`.")
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        raise ErroPR("gh não está autenticado. Rode `gh auth login`.")
    if not _rodar("git", "remote", checar=False):
        raise ErroPR("O repositório não tem remote (git remote add origin ...).")


def branch_padrao() -> str:
    nome = _rodar("gh", "repo", "view", "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name", checar=False)
    return nome or "main"


def abrir_pr(branch: str, titulo: str, corpo_arquivo: str, caminhos: list[str], mensagem: str) -> str | None:
    """Commita `caminhos` num branch fixo, faz push e cria/atualiza o PR. Devolve a URL."""
    _pre_requisitos()
    mudancas = _rodar("git", "status", "--porcelain", "--", *caminhos)
    if not mudancas:
        print("Nada mudou nos arquivos; nenhum PR aberto/atualizado.")
        return None

    original = _rodar("git", "rev-parse", "--abbrev-ref", "HEAD")
    base = branch_padrao()
    if original == branch:
        raise ErroPR(f"Você está no branch {branch}; rode a partir de {base}.")

    _rodar("git", "switch", "-C", branch)
    try:
        _rodar("git", "add", "--", *caminhos)
        _rodar("git", "commit", "-m", mensagem, "--", *caminhos)
        _rodar("git", "push", "--force", "-u", "origin", branch)
    finally:
        _rodar("git", "switch", original)

    existente = _rodar("gh", "pr", "list", "--head", branch, "--state", "open",
                       "--json", "url", "-q", ".[0].url", checar=False)
    if existente:
        _rodar("gh", "pr", "edit", existente, "--title", titulo, "--body-file", corpo_arquivo)
        print(f"PR atualizado: {existente}")
        return existente
    url = _rodar("gh", "pr", "create", "--base", base, "--head", branch,
                 "--title", titulo, "--body-file", corpo_arquivo)
    print(f"PR aberto: {url}")
    return url
