"""Reprova a versão de uma peça que está em production e volta para a anterior.

Mexe só no producao.lock.json (e aplica, com --aplicar):
  1. registra a versão atual em "reprovadas" (commit, motivo, quem, quando);
  2. troca a entrada da peça pela versão anterior válida, procurada no histórico
     do próprio lock (ou a indicada com --para-sha).

Enquanto a versão reprovada for a que está em @staging, o prompts-promote não a
propõe de novo: production fica na anterior até sair uma versão nova da peça.

Uso:
    uv run prompts-reject --only atendimento-formato-default --motivo "respostas longas"
    uv run prompts-reject --only atendimento-formato-default --motivo "…" --para-sha 1a2b3c4
    uv run prompts-reject --only atendimento-formato-default --desfazer --git-sha 90094fa
    uv run prompts-reject … --aplicar        # já aplica em production (o workflow faz isso)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

from .lock import ARQUIVO, entrada, escrever_lock, ler_lock
from .registry import git


def historico(nome: str) -> list[dict]:
    """Entradas que a peça já teve no lock, da mais recente para a mais antiga (sem repetir)."""
    vistos, saida = set(), []
    for commit in (git("log", "--format=%H", "--", ARQUIVO) or "").splitlines():
        bruto = git("show", f"{commit}:{ARQUIVO}", strip=False)
        e = (json.loads(bruto).get("prompts", {}) if bruto else {}).get(nome)
        if e and e["git_sha"] not in vistos:
            vistos.add(e["git_sha"])
            saida.append(e)
    return saida


def quem() -> str:
    return os.environ.get("GITHUB_ACTOR") or git("config", "user.name") or "desconhecido"


def reprovar(nome: str, motivo: str, para_sha: str | None) -> None:
    lock = ler_lock()
    atual = lock["prompts"].get(nome)
    if atual is None:
        raise SystemExit(f"{nome} não está no lock de production.")
    lock["reprovadas"].append({
        "prompt": nome, "git_sha": atual["git_sha"], "source_path": atual["source_path"],
        "motivo": motivo, "por": quem(), "em": dt.date.today().isoformat(),
    })
    reprovados = {r["git_sha"] for r in lock["reprovadas"] if r["prompt"] == nome}

    if para_sha:
        alvo = entrada(para_sha, atual["source_path"])
    else:
        candidatos = [e for e in historico(nome) if e["git_sha"] not in reprovados]
        if not candidatos:
            raise SystemExit(f"{nome}: não há versão anterior não reprovada no histórico do lock; use --para-sha.")
        alvo = candidatos[0]

    lock["prompts"][nome] = alvo
    escrever_lock(lock)
    print(f"{nome}: {atual['git_sha']} reprovada ({motivo}); production -> {alvo['git_sha']}")


def desfazer(nome: str, git_sha: str) -> None:
    lock = ler_lock()
    antes = len(lock["reprovadas"])
    lock["reprovadas"] = [r for r in lock["reprovadas"] if not (r["prompt"] == nome and r["git_sha"] == git_sha)]
    escrever_lock(lock)
    print(f"{nome}: {git_sha} {'não está mais reprovada' if len(lock['reprovadas']) < antes else 'não estava reprovada'}"
          " (promova de novo se quiser usá-la)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--only", required=True, help="peça em production (ex.: atendimento-formato-default)")
    p.add_argument("--motivo", help="obrigatório ao reprovar")
    p.add_argument("--para-sha", help="commit da versão para onde voltar")
    p.add_argument("--desfazer", action="store_true")
    p.add_argument("--git-sha", help="com --desfazer: commit da versão a tirar da lista de reprovadas")
    p.add_argument("--aplicar", action="store_true", help="aplica o lock em production em seguida")
    a = p.parse_args()

    if a.desfazer:
        if not a.git_sha:
            p.error("--desfazer exige --git-sha")
        desfazer(a.only, a.git_sha)
    else:
        if not a.motivo or len(a.motivo.strip()) < 10:
            p.error("--motivo é obrigatório (mínimo 10 caracteres)")
        reprovar(a.only, a.motivo.strip(), a.para_sha)

    if a.aplicar:
        from .aplicar import aplicar

        raise SystemExit(aplicar())


if __name__ == "__main__":
    main()
