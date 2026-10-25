"""Lado da aplicação: nenhum texto de prompt aqui, só nomes e aliases.

A montagem (base + seções, com fallback para o padrão) fica em composicao.py.

Uso:
    uv run atendimento-demo                        # cartao e seguro em production
    uv run atendimento-demo --ambiente staging --produto seguro
"""
from __future__ import annotations

import argparse

from .composicao import montar, produtos


def montar_mensagens(produto: str, pergunta: str, ambiente: str = "production") -> list[dict]:
    return [
        {"role": "system", "content": montar(produto, ambiente).texto},
        {"role": "user", "content": pergunta},
    ]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ambiente", default="production")
    p.add_argument("--produto", action="append", help="repita para vários produtos")
    p.add_argument("--pergunta", default="Qual é o meu limite?")
    p.add_argument("--so-fontes", action="store_true", help="mostra só de onde veio cada seção")
    a = p.parse_args()

    for produto in a.produto or produtos():
        montado = montar(produto, a.ambiente)
        print(f"===== {produto} @{a.ambiente} =====")
        for secao, fonte in montado.fontes.items():
            print(f"  {secao:<11} <- {fonte}")
        if not a.so_fontes:
            print("\n" + montado.texto)
            print(f"[user] {a.pergunta}")
        print()


if __name__ == "__main__":
    main()
