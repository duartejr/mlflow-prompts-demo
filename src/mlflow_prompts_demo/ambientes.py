"""Ambientes (staging, production) e o servidor MLflow de cada um.

Um servidor só (padrão):
    MLFLOW_TRACKING_URI=http://mlflow:5000
    -> staging e production são aliases no mesmo registro.

Dois servidores:
    MLFLOW_TRACKING_URI_STAGING=http://mlflow-staging:5000
    MLFLOW_TRACKING_URI_PRODUCTION=http://mlflow-prod:5000
    -> cada ambiente tem o seu registro. Os números de versão NÃO batem entre eles
       (a v5 de staging pode ser a v3 de production); a identidade de uma versão é
       o texto + o commit do Git (tags git_sha e source_path).

Credenciais por servidor seguem o mesmo padrão de sufixo:
    MLFLOW_TRACKING_USERNAME_STAGING, MLFLOW_TRACKING_PASSWORD_PRODUCTION,
    MLFLOW_TRACKING_TOKEN_PRODUCTION, ...
Sem sufixo, valem para os dois.

Em cada servidor o alias tem o nome do ambiente: @staging no de staging,
@production no de production.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import mlflow

AMBIENTES = ("staging", "production")
_CREDENCIAIS = ("MLFLOW_TRACKING_USERNAME", "MLFLOW_TRACKING_PASSWORD", "MLFLOW_TRACKING_TOKEN")


def _valor(chave: str, ambiente: str) -> str | None:
    return os.environ.get(f"{chave}_{ambiente.upper()}") or os.environ.get(chave)


def uri(ambiente: str) -> str:
    if ambiente not in AMBIENTES:
        raise SystemExit(f"Ambiente desconhecido: {ambiente}. Use {' ou '.join(AMBIENTES)}.")
    valor = _valor("MLFLOW_TRACKING_URI", ambiente)
    if not valor:
        raise SystemExit(
            f"Defina MLFLOW_TRACKING_URI (um servidor) ou MLFLOW_TRACKING_URI_{ambiente.upper()}."
        )
    return valor


def dois_servidores() -> bool:
    return uri("staging") != uri("production")


@contextmanager
def servidor(ambiente: str):
    """Aponta o cliente MLflow (URI e credenciais) para o servidor do ambiente."""
    anterior_uri = mlflow.get_tracking_uri()
    anteriores = {c: os.environ.get(c) for c in _CREDENCIAIS}
    mlflow.set_tracking_uri(uri(ambiente))
    for chave in _CREDENCIAIS:
        valor = _valor(chave, ambiente)
        if valor is None:
            os.environ.pop(chave, None)
        else:
            os.environ[chave] = valor
    try:
        yield
    finally:
        mlflow.set_tracking_uri(anterior_uri)
        for chave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor
