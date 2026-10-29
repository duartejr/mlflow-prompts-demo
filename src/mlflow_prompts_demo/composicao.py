"""Montagem do prompt completo de um produto: base + uma peça por seção.

O base declara as seções como variáveis ({{ formato }}, {{ restricoes }}).
Para cada seção, procura-se, com o mesmo alias:

    1. <dominio>-<secao>-<produto>   (override do produto)
    2. <dominio>-<secao>-default     (padrão da seção)

Se nenhum dos dois existir, a seção é obrigatória e o produto está incompleto: erro.

O base pode usar Jinja ({% if canal == "whatsapp" %}). As variáveis de execução
(produto, canal) não são seções; as demais saem do próprio template, inclusive
as usadas só dentro de {% %} (ver variaveis.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import mlflow

from .ambientes import servidor
from .registry import listar_arquivos, pasta_prompts
from .variaveis import renderizar, variaveis

DOMINIO = "atendimento"
BASE = f"{DOMINIO}-base"
PADRAO = "default"
EXECUCAO = {"produto", "canal"}  # variáveis preenchidas na chamada, não seções
CANAIS = ("whatsapp", "email")


@dataclass
class PromptMontado:
    produto: str
    ambiente: str
    texto: str
    fontes: dict[str, str]  # seção -> "nome@vN"
    base: object = None  # PromptVersion do base usado
    pecas: dict[str, object] = field(default_factory=dict)  # seção -> PromptVersion usada


def _carregar(uri: str, cache_ttl: float | None, allow_missing: bool = False):
    return mlflow.genai.load_prompt(uri, allow_missing=allow_missing, cache_ttl_seconds=cache_ttl)


def carregar_secao(secao: str, produto: str, ambiente: str, cache_ttl: float | None = None):
    """Override do produto, senão o padrão da seção, sempre no mesmo alias."""
    for alvo in (produto, PADRAO):
        prompt = _carregar(
            f"prompts:/{DOMINIO}-{secao}-{alvo}@{ambiente}", cache_ttl, allow_missing=True
        )
        if prompt is not None:
            return prompt
    raise LookupError(
        f"Seção '{secao}' sem override para '{produto}' e sem padrão em @{ambiente}. "
        f"Crie prompts/{DOMINIO}/{secao}/{produto}.md ou prompts/{DOMINIO}/{secao}/{PADRAO}.md."
    )


def secoes(template_base: str) -> list[str]:
    return sorted(variaveis(template_base) - EXECUCAO)


def montar(produto: str, ambiente: str = "production", cache_ttl: float | None = None,
           canal: str | None = None) -> PromptMontado:
    """Monta no servidor do ambiente, com o alias do ambiente (@staging ou @production).

    cache_ttl=None usa o cache padrão do MLflow (bom para a aplicação, que roda
    num ambiente só); os scripts passam 0 para ler o estado real.
    canal só é necessário se o base usar {{ canal }}; se usar e faltar, é erro.
    """
    with servidor(ambiente):
        base = _carregar(f"prompts:/{BASE}@{ambiente}", cache_ttl)
        fontes = {"base": f"{base.name}@v{base.version}"}
        valores = {"produto": produto} | ({"canal": canal} if canal else {})
        pecas = {}

        for secao in secoes(base.template):
            prompt = carregar_secao(secao, produto, ambiente, cache_ttl)
            valores[secao] = prompt.template.strip()
            fontes[secao] = f"{prompt.name}@v{prompt.version}"
            pecas[secao] = prompt

    return PromptMontado(produto, ambiente, renderizar(base.template, **valores), fontes, base, pecas)


def produtos() -> list[str]:
    """Produtos conhecidos: nomes de arquivo dentro das pastas de seção, exceto default."""
    raiz = pasta_prompts() / DOMINIO
    encontrados = set()
    for arquivo in listar_arquivos():
        partes = arquivo.relative_to(raiz).with_suffix("").parts if arquivo.is_relative_to(raiz) else ()
        if len(partes) == 2 and partes[1] != PADRAO:
            encontrados.add(partes[1])
    return sorted(encontrados)
