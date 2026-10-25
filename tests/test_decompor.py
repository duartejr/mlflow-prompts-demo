"""Testes offline da decomposição (edição no prompt final -> peças)."""
from mlflow.entities.model_registry import PromptVersion

from mlflow_prompts_demo.decompor import decompor

BASE = (
    "Você é o assistente do produto {{ produto }}.\n\n"
    "## Formato\n{{ formato }}\n\n"
    "## Restrições\n{{ restricoes }}\n"
)
VALORES = {
    "formato": "- Responda em até 5 frases.\n- Se não souber, transfira.",
    "restricoes": "- Não interprete cláusulas.\n- Não confirme sinistro.",
}


def montado(base=BASE, **valores):
    return PromptVersion(name="b", version=1, template=base).format(produto="seguro", **{**VALORES, **valores})


def test_sem_mudanca():
    r = decompor(BASE, "seguro", VALORES, montado())
    assert r.erro is None and r.secoes == {} and r.novo_base is None


def test_linha_nova_no_fim_de_uma_secao():
    editado = montado().replace("- Se não souber, transfira.", "- Se não souber, transfira.\n- Use linguagem simples.")
    r = decompor(BASE, "seguro", VALORES, editado)
    assert r.erro is None and r.novo_base is None
    assert r.secoes == {"formato": VALORES["formato"] + "\n- Use linguagem simples."}


def test_duas_secoes_mudam():
    editado = montado().replace("até 5 frases", "até 3 frases").replace("Não confirme sinistro", "Nunca confirme sinistro")
    r = decompor(BASE, "seguro", VALORES, editado)
    assert set(r.secoes) == {"formato", "restricoes"} and r.novo_base is None


def test_mover_linha_entre_secoes():
    editado = montado(
        formato="- Responda em até 5 frases.",
        restricoes="- Se não souber, transfira.\n- Não interprete cláusulas.\n- Não confirme sinistro.",
    )
    r = decompor(BASE, "seguro", VALORES, editado)
    assert r.erro is None and set(r.secoes) == {"formato", "restricoes"}


def test_texto_fixo_do_base_alterado():
    editado = montado().replace("Você é o assistente", "Você é a assistente virtual")
    r = decompor(BASE, "seguro", VALORES, editado)
    assert r.erro is None and r.secoes == {}
    assert r.novo_base.startswith("Você é a assistente virtual do produto {{ produto }}.")


def test_base_e_secao_alterados_juntos():
    editado = montado().replace("## Restrições", "## Proibido").replace("até 5 frases", "até 4 frases")
    r = decompor(BASE, "seguro", VALORES, editado)
    assert r.erro is None
    assert "## Proibido\n{{ restricoes }}" in r.novo_base
    assert r.secoes == {"formato": VALORES["formato"].replace("5", "4")}


def test_final_de_arquivo_e_crlf_da_interface():
    editado = montado().replace("\n", "\r\n").rstrip() + "   \r\n"
    r = decompor(BASE, "seguro", VALORES, editado.rstrip("\r\n"))
    assert r.erro is None and r.secoes == {}


def test_nome_do_produto_alterado_vira_erro():
    editado = montado().replace("produto seguro", "produto seguro auto")
    r = decompor(BASE, "seguro", VALORES, editado)
    assert r.erro is not None
