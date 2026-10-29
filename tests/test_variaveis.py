"""Testes offline do modo Jinja: variáveis, renderização estrita e montagem por canal."""
from pathlib import Path

import jinja2
import pytest
from mlflow.entities.model_registry import PromptVersion

from mlflow_prompts_demo.variaveis import eh_jinja, renderizar, variaveis
from mlflow_prompts_demo.verificar import compor, ler_arquivos

JINJA = (
    "Produto {{ produto }}.\n"
    '{% if canal == "whatsapp" %}- 3 frases{% else %}{{ formato }}{% endif %}\n'
    "{% for regra in regras %}- {{ regra }}\n{% endfor %}"
)


def test_modo_jinja_so_com_tag():
    assert eh_jinja(JINJA)
    assert not eh_jinja("Produto {{ produto }}.")


def test_variables_do_mlflow_erra_no_modo_jinja():
    # O MLflow lê {{ }} com regex: pega a variável do laço e perde canal e regras.
    assert PromptVersion("b", 1, JINJA).variables == {"produto", "formato", "regra"}
    assert variaveis(JINJA) == {"produto", "canal", "formato", "regras"}


def test_mlflow_aceita_variavel_faltando_no_modo_jinja():
    texto = PromptVersion("b", 1, JINJA).format(produto="cartao")
    assert texto == "Produto cartao.\n\n"


def test_renderizar_exige_todas_as_variaveis():
    with pytest.raises(jinja2.UndefinedError, match="canal"):
        renderizar(JINJA, produto="cartao")
    assert renderizar(JINJA, produto="cartao", canal="whatsapp", regras=["a"]) == "Produto cartao.\n- 3 frases\n- a\n"


def test_modo_nativo_continua_igual_ao_mlflow():
    nativo = "Produto {{ produto }}, formato {{ formato }}."
    assert variaveis(nativo) == {"produto", "formato"}
    assert renderizar(nativo, produto="a", formato="b") == PromptVersion("b", 1, nativo).format(produto="a", formato="b")


def escrever(raiz: Path, arquivos: dict[str, str]) -> None:
    for caminho, texto in arquivos.items():
        destino = raiz / "atendimento" / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")


@pytest.fixture
def prompts(tmp_path, monkeypatch):
    raiz = tmp_path / "prompts"
    monkeypatch.setenv("PROMPTS_DIR", str(raiz))
    return raiz


def test_secao_usada_so_dentro_de_if_e_encontrada(prompts):
    escrever(prompts, {
        "base.md": 'Produto {{ produto }}.\n{% if canal == "email" %}{{ formato }}{% endif %}\n',
        "formato/default.md": "- padrão\n",
        "formato/cartao.md": "- curto\n",
    })
    r = compor(ler_arquivos(None))
    assert r.erros == []
    assert set(r.textos) == {"cartao · whatsapp", "cartao · email"}
    assert "- curto" in r.textos["cartao · email"]
    assert "- curto" not in r.textos["cartao · whatsapp"]


def test_variavel_sem_peca_vira_erro(prompts):
    escrever(prompts, {
        "base.md": "Produto {{ produto }}.\n{% for regra in regras %}- {{ regra }}\n{% endfor %}",
        "formato/cartao.md": "- curto\n",
    })
    r = compor(ler_arquivos(None))
    assert any("seção 'regras'" in e for e in r.erros)
