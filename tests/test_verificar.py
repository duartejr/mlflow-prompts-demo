"""Testes offline da montagem e da validação (não precisam de servidor MLflow)."""
from pathlib import Path

import pytest

from mlflow_prompts_demo.verificar import compor, ler_arquivos

BASE = "Produto {{ produto }}.\n## Formato\n{{ formato }}\n## Restrições\n{{ restricoes }}\n"


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


def test_override_e_default(prompts):
    escrever(prompts, {
        "base.md": BASE,
        "formato/default.md": "- padrão\n",
        "formato/cartao.md": "- curto\n",
        "restricoes/cartao.md": "- sem limite\n",
        "restricoes/seguro.md": "- sem cláusula\n",
    })
    r = compor(ler_arquivos(None))
    assert r.erros == []
    assert r.fontes["cartao"]["formato"] == "atendimento/formato/cartao.md"
    assert r.fontes["seguro"]["formato"] == "atendimento/formato/default.md"
    assert "- curto" in r.textos["cartao"] and "- padrão" in r.textos["seguro"]
    assert "{{" not in r.textos["seguro"]


def test_secao_obrigatoria_sem_default(prompts):
    escrever(prompts, {
        "base.md": BASE,
        "formato/default.md": "- padrão\n",
        "restricoes/cartao.md": "- x\n",
        "formato/seguro.md": "- y\n",  # seguro existe, mas não tem restrições
    })
    r = compor(ler_arquivos(None))
    assert any("'seguro': seção 'restricoes'" in e for e in r.erros)
    assert "seguro" not in r.textos


def test_variavel_em_bloco_de_secao(prompts):
    escrever(prompts, {
        "base.md": BASE,
        "formato/default.md": "- use {{ tom }}\n",
        "restricoes/cartao.md": "- x\n",
    })
    r = compor(ler_arquivos(None))
    assert any("não podem ter variáveis" in e for e in r.erros)


def test_secao_nao_usada_gera_aviso(prompts):
    escrever(prompts, {
        "base.md": BASE,
        "formato/default.md": "- p\n",
        "restricoes/cartao.md": "- x\n",
        "tom/default.md": "- amigável\n",
    })
    r = compor(ler_arquivos(None))
    assert r.erros == []
    assert any("tom/" in a for a in r.avisos)


def test_descricao_do_pr():
    from mlflow_prompts_demo.verificar import descricao_valida

    assert not descricao_valida("")
    assert not descricao_valida("## Descrição\n<!-- explique o motivo -->\n\n## Checklist\n- [ ] ok")
    assert not descricao_valida("## Descrição\ncurto\n")
    assert descricao_valida("## Descrição\nPromove o formato padrão com linguagem simples, validado em staging.\n")
