"""Decomposição: de uma edição no prompt FINAL para as peças certas.

Uma pessoa não técnica pode editar o texto completo de um produto na interface do
MLflow (prompt `montado-atendimento-<produto>`, botão "Create prompt version").
Este módulo descobre o que mudou em cada peça:

1. O base é um molde: texto fixo com lacunas ({{ formato }}, {{ restricoes }}).
   Vira uma expressão regular (texto fixo literal, cada lacuna captura "o que
   estiver ali"). Se o texto editado casa, o texto fixo não mudou e cada grupo
   é o novo conteúdo de uma seção.
2. Se não casa, a pessoa mexeu no texto fixo do base. Aí um diff caractere a
   caractere entre o texto montado atual e o editado é distribuído pelos trechos
   (texto fixo / seção). Mudança que atravessa a fronteira entre dois trechos não
   tem dono claro e vira pedido de revisão manual.
3. Conferência: remontar com as peças novas tem de reproduzir exatamente o texto
   editado. Se não reproduz, nada é aplicado.

Onde a mudança vai parar:
  - seção que já é própria do produto        -> nova versão dessa peça;
  - seção herdada do padrão (default)        -> novo override só para o produto
    (é exatamente o que a pessoa testou); o PR lista quem mudaria se fosse no padrão;
  - texto fixo do base                       -> nova versão do base (afeta TODOS).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from mlflow.entities.model_registry import PromptVersion

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass
class Decomposicao:
    secoes: dict[str, str] = field(default_factory=dict)  # seção -> novo conteúdo (só as que mudaram)
    novo_base: str | None = None  # template novo, se o texto fixo mudou
    metodo: str = ""
    erro: str | None = None


def _segmentos(template: str, produto: str, valores: dict[str, str]) -> list[tuple[str, str | None, str]]:
    segs, pos = [], 0
    for m in PLACEHOLDER.finditer(template):
        if m.start() > pos:
            segs.append(("literal", None, template[pos:m.start()]))
        var = m.group(1)
        segs.append(("produto", var, produto) if var == "produto" else ("secao", var, valores[var]))
        pos = m.end()
    if pos < len(template):
        segs.append(("literal", None, template[pos:]))
    return segs


def _limpar(texto: str) -> str:
    """Sem espaços no fim das linhas (a interface às vezes deixa)."""
    return "\n".join(linha.rstrip(" \t") for linha in texto.split("\n"))


def _normalizar(editado: str, referencia: str) -> str:
    """Quebras de linha Unix, sem espaços no fim das linhas e o mesmo final de arquivo da referência."""
    editado = _limpar(editado.replace("\r\n", "\n"))
    final = referencia[len(referencia.rstrip("\n")):]
    return editado.rstrip("\n") + final


def _por_regex(segs, editado: str) -> dict[str, str] | None:
    partes, vistos = ["^"], set()
    for tipo, var, texto in segs:
        if tipo in ("literal", "produto"):
            partes.append(re.escape(texto))
        elif var in vistos:
            partes.append(f"(?P={var})")
        else:
            partes.append(f"(?P<{var}>.*?)")
            vistos.add(var)
    partes.append("$")
    m = re.match("".join(partes), editado, re.DOTALL)
    return {v: m.group(v) for v in vistos} if m else None


def _mapear(fronteira: int, ops, insercao_vai_para_esquerda: bool) -> int | None:
    """Posição no texto editado correspondente a uma fronteira do texto original."""
    for tag, i1, i2, j1, j2 in ops:
        if tag == "insert" and i1 == fronteira:
            return j2 if insercao_vai_para_esquerda else j1
    for tag, i1, i2, j1, j2 in ops:
        if i1 <= fronteira <= i2 and tag != "insert":
            if tag == "equal":
                return j1 + (fronteira - i1)
            if fronteira == i1:
                return j1
            if fronteira == i2:
                return j2
            return None  # fronteira no meio de um trecho alterado
    return None


def _por_diff(segs, montado: str, editado: str):
    ops = difflib.SequenceMatcher(None, montado, editado, autojunk=False).get_opcodes()
    limites = [0]
    for _, _, texto in segs:
        limites.append(limites[-1] + len(texto))
    novos = []
    pos_ed = [0]
    for k in range(1, len(segs)):
        esquerda_e_secao = segs[k - 1][0] == "secao"
        j = _mapear(limites[k], ops, insercao_vai_para_esquerda=esquerda_e_secao)
        if j is None:
            return None
        pos_ed.append(j)
    pos_ed.append(len(editado))
    if any(b < a for a, b in zip(pos_ed, pos_ed[1:])):
        return None
    for k, (tipo, var, _) in enumerate(segs):
        novos.append((tipo, var, editado[pos_ed[k]:pos_ed[k + 1]]))
    return novos


def decompor(template_base: str, produto: str, valores: dict[str, str], editado: str) -> Decomposicao:
    """valores: conteúdo atual (strip) de cada seção para este produto."""
    segs = _segmentos(template_base, produto, valores)
    montado = "".join(t for _, _, t in segs)
    editado = _normalizar(editado, montado)
    if editado == montado:
        return Decomposicao(metodo="sem mudança")

    r = Decomposicao()
    capturas = _por_regex(segs, editado)
    if capturas is not None:
        r.metodo = "texto fixo do base intacto; mudanças só nas seções"
        for var, texto in capturas.items():
            if texto.strip() != valores[var].strip():
                r.secoes[var] = texto.strip()
    else:
        novos = _por_diff(segs, montado, editado)
        if novos is None:
            return Decomposicao(erro="a edição atravessa a fronteira entre o texto fixo do base e uma seção; "
                                     "não dá para saber a qual peça cada parte pertence")
        r.metodo = "texto fixo do base alterado"
        literal_mudou = False
        conteudo: dict[str, str] = {}
        partes_base = []
        for (tipo, var, novo), (_, _, velho) in zip(novos, segs):
            if tipo == "literal":
                partes_base.append(novo)
                literal_mudou |= novo != velho
            elif tipo == "produto":
                if novo != velho:
                    return Decomposicao(erro=f"o nome do produto ('{produto}') foi alterado no texto")
                partes_base.append("{{ produto }}")
            else:
                if var in conteudo and conteudo[var].strip() != novo.strip():
                    return Decomposicao(erro=f"a seção '{var}' aparece duas vezes e foi editada de formas diferentes")
                conteudo[var] = novo
                partes_base.append(f"{{{{ {var} }}}}")
        if literal_mudou:
            r.novo_base = "".join(partes_base)
        for var, texto in conteudo.items():
            if texto.strip() != valores[var].strip():
                r.secoes[var] = texto.strip()

    # Conferência: remontar tem de dar exatamente o texto editado
    base = PromptVersion(name="base", version=1, template=r.novo_base or template_base)
    remontado = base.format(produto=produto, **{**valores, **r.secoes})
    if _limpar(remontado) != editado:
        return Decomposicao(erro="remontar com as peças novas não reproduz o texto editado "
                                 "(provavelmente espaços ou linhas em branco nas bordas de uma seção)")
    return r
