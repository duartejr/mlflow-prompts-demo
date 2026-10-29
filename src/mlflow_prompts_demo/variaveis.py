"""Variáveis e renderização de um template nos dois modos do MLflow.

O MLflow 3.x formata um prompt de dois jeitos (PromptVersion.format):

  - modo nativo: o template só tem {{ variavel }}. O MLflow troca cada uma e,
    se faltar alguma, levanta erro ("Missing variables").
  - modo Jinja: o template contém {% ... %}. O MLflow renderiza com o
    SandboxedEnvironment do Jinja2, sem loader e com o Undefined padrão.

No modo Jinja há três diferenças que importam para quem monta prompts:

  1. variável que falta vira texto vazio, sem erro (um {% if %} cai no else,
     um {% for %} não gera nada);
  2. PromptVersion.variables continua lendo o texto com uma regex de {{ }}:
     lista variáveis de laço e não lista as usadas só dentro de {% %};
  3. {% include %} e {% extends %} não funcionam (o ambiente não tem loader).

Este módulo usa o próprio Jinja2 para listar as variáveis do template e
renderiza com StrictUndefined, para que a falta de uma variável vire erro.
"""
from __future__ import annotations

import jinja2
import jinja2.meta
from jinja2.sandbox import SandboxedEnvironment
from mlflow.entities.model_registry import PromptVersion


def eh_jinja(template: str) -> bool:
    """Mesma regra do MLflow: o modo Jinja liga quando há {% e %} no texto."""
    return "{%" in template and "%}" in template


def variaveis(template: str) -> set[str]:
    """Variáveis que o template espera receber, nos dois modos."""
    if not eh_jinja(template):
        return PromptVersion(name="x", version=1, template=template).variables
    ast = SandboxedEnvironment().parse(template)
    return set(jinja2.meta.find_undeclared_variables(ast))


def renderizar(template: str, **valores) -> str:
    """Formata como o MLflow, mas com erro quando falta variável no modo Jinja."""
    if not eh_jinja(template):
        return PromptVersion(name="x", version=1, template=template).format(**valores)
    env = SandboxedEnvironment(undefined=jinja2.StrictUndefined)
    return env.from_string(template).render(**valores)
