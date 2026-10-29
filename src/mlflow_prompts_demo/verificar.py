"""Valida prompts/ e mostra o prompt completo de cada produto SEM servidor MLflow.

Feito para o PR: lê os arquivos (do working tree ou de um ref do Git), aplica a
mesma regra de montagem da aplicação (override do produto, senão default) e
formata como a aplicação (variaveis.renderizar), então o texto é idêntico
ao que a aplicação vai montar depois do sync.

Checagens (erro = código de saída 1):
  - existe <dominio>/base.md;
  - todo produto resolve todas as seções do base (override ou default);
  - blocos de seção não têm {{ variáveis }};
  - o base renderiza sem variável faltando (com StrictUndefined, também no
    modo Jinja); se o base usa {{ canal }}, cada produto é montado em cada canal;
Avisos:
  - pasta de seção que o base não usa.

Uso:
    uv run prompts-check                              # valida e lista as fontes
    uv run prompts-check --mostrar                    # + texto completo de cada produto
    uv run prompts-check --diff-ref origin/main       # diff do prompt completo vs main
    uv run prompts-check --diff-ref origin/main --summary   # em Markdown (job summary)
    PR_BODY="..." uv run prompts-check --diff-ref origin/main --exigir-descricao

Também valida o producao.lock.json (cada peça existe no commit indicado, com o
mesmo texto) e, se o PR muda o lock, mostra o que muda em production. Com
--exigir-descricao, um PR que muda o lock precisa ter a seção "## Descrição"
preenchida (pelo menos 30 caracteres).
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import jinja2
from mlflow.exceptions import MlflowException

from .composicao import CANAIS, DOMINIO, PADRAO, secoes as secoes_do_base
from .lock import ler_lock, resumo_mudancas, validar_lock
from .registry import git, pasta_prompts
from .variaveis import renderizar, variaveis

BASE = "base"


@dataclass
class Resultado:
    textos: dict[str, str] = field(default_factory=dict)        # produto -> prompt completo
    fontes: dict[str, dict[str, str]] = field(default_factory=dict)  # produto -> seção -> arquivo
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _prefixo_git() -> str:
    raiz = git("rev-parse", "--show-toplevel")
    return os.path.relpath(pasta_prompts(), raiz).replace(os.sep, "/") if raiz else "prompts"


def ler_arquivos(ref: str | None) -> dict[PurePosixPath, str]:
    """Caminho relativo a prompts/<dominio>/ -> texto. ref=None lê o disco."""
    if ref is None:
        raiz = pasta_prompts() / DOMINIO
        return {
            PurePosixPath(a.relative_to(raiz).as_posix()): a.read_text(encoding="utf-8")
            for a in sorted(raiz.rglob("*.md"))
        }
    prefixo = f"{_prefixo_git()}/{DOMINIO}"
    nomes = (git("ls-tree", "-r", "--name-only", ref, "--", prefixo) or "").splitlines()
    return {
        PurePosixPath(n).relative_to(prefixo): git("show", f"{ref}:{n}", strip=False) or ""
        for n in nomes if n.endswith(".md")
    }


def compor(arquivos: dict[PurePosixPath, str]) -> Resultado:
    r = Resultado()
    base_txt = arquivos.get(PurePosixPath(f"{BASE}.md"))
    if base_txt is None:
        r.erros.append(f"{DOMINIO}/{BASE}.md não existe")
        return r
    secoes = secoes_do_base(base_txt)
    canais = CANAIS if "canal" in variaveis(base_txt) else (None,)

    pecas: dict[tuple[str, str], str] = {}  # (secao, produto|default) -> texto
    for caminho, texto in arquivos.items():
        if len(caminho.parts) != 2:
            continue
        secao, alvo = caminho.parts[0], caminho.stem
        pecas[(secao, alvo)] = texto
        if usadas := variaveis(texto):
            r.erros.append(f"{DOMINIO}/{caminho}: blocos de seção não podem ter variáveis ({', '.join(sorted(usadas))})")
    for secao in sorted({s for s, _ in pecas} - set(secoes)):
        r.avisos.append(f"{DOMINIO}/{secao}/ existe, mas o base não tem {{{{ {secao} }}}}")

    produtos = sorted({alvo for (_, alvo) in pecas if alvo != PADRAO})
    for produto in produtos:
        valores, fontes = {"produto": produto}, {"base": f"{DOMINIO}/{BASE}.md"}
        for secao in secoes:
            for alvo in (produto, PADRAO):
                if (secao, alvo) in pecas:
                    valores[secao] = pecas[(secao, alvo)].strip()
                    fontes[secao] = f"{DOMINIO}/{secao}/{alvo}.md"
                    break
            else:
                r.erros.append(f"produto '{produto}': seção '{secao}' sem {DOMINIO}/{secao}/{produto}.md e sem {PADRAO}.md")
        if len(fontes) != len(secoes) + 1:
            continue
        for canal in canais:
            chave = produto if canal is None else f"{produto} · {canal}"
            try:
                r.textos[chave] = renderizar(base_txt, **valores, **({"canal": canal} if canal else {}))
            except (jinja2.UndefinedError, MlflowException) as e:
                r.erros.append(f"produto '{chave}': o base não renderiza ({e})")
                continue
            r.fontes[chave] = fontes
    return r


def relatorio(atual: Resultado, anterior: Resultado | None, ref: str | None, markdown: bool, mostrar: bool) -> str:
    linhas: list[str] = []
    titulo = (lambda t: f"### {t}") if markdown else (lambda t: f"===== {t} =====")

    if atual.erros:
        linhas.append(titulo("Erros"))
        linhas += [f"- {e}" for e in atual.erros]
    if atual.avisos:
        linhas.append(titulo("Avisos"))
        linhas += [f"- {a}" for a in atual.avisos]

    produtos = sorted(set(atual.textos) | set(anterior.textos if anterior else []))
    for produto in produtos:
        novo = atual.textos.get(produto)
        velho = anterior.textos.get(produto) if anterior else None
        if anterior is not None and novo == velho:
            linhas.append(f"{'- ' if markdown else ''}`{produto}`: prompt completo sem mudança em relação a `{ref}`")
            continue
        linhas.append(titulo(f"Produto {produto}"))
        for secao, arquivo in (atual.fontes.get(produto) or {}).items():
            linhas.append(f"{'- ' if markdown else '  '}{secao}: `{arquivo}`")
        if anterior is not None:
            diff = "\n".join(difflib.unified_diff(
                (velho or "").splitlines(), (novo or "").splitlines(),
                fromfile=f"{produto}@{ref}", tofile=f"{produto}@PR", lineterm="",
            ))
            linhas += ["```diff" if markdown else "", diff, "```" if markdown else ""]
        elif mostrar and novo:
            linhas += ["```markdown" if markdown else "", novo, "```" if markdown else ""]
    return "\n".join(linhas)


def descricao_valida(corpo: str) -> bool:
    """Seção '## Descrição' com pelo menos 30 caracteres que não sejam comentário HTML."""
    m = re.search(r"^##\s*Descri[cç][aã]o\s*$(.*?)(?=^##\s|\Z)", corpo or "", re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not m:
        return False
    texto = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.DOTALL).strip()
    return len(texto) >= 30


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--diff-ref", help="ref do Git para comparar (ex.: origin/main)")
    p.add_argument("--mostrar", action="store_true", help="mostra o texto completo de cada produto")
    p.add_argument("--summary", action="store_true", help="Markdown; anexa ao $GITHUB_STEP_SUMMARY se existir")
    p.add_argument("--exigir-descricao", action="store_true",
                   help="se o lock mudou, exige '## Descrição' preenchida no corpo do PR (variável PR_BODY)")
    a = p.parse_args()

    atual = compor(ler_arquivos(None))
    anterior = compor(ler_arquivos(a.diff_ref)) if a.diff_ref else None

    lock = ler_lock()
    atual.erros += validar_lock(lock)
    lock_mudou = bool(a.diff_ref) and ler_lock(a.diff_ref) != lock
    if lock_mudou and a.exigir_descricao and not descricao_valida(os.environ.get("PR_BODY", "")):
        atual.erros.append("este PR muda production (producao.lock.json): preencha a seção '## Descrição' "
                           "do PR com o motivo da promoção (mínimo 30 caracteres)")

    texto = relatorio(atual, anterior, a.diff_ref, a.summary, a.mostrar)
    if lock_mudou:
        texto += "\n\n## Production: o que este PR muda\n\n" + resumo_mudancas(ler_lock(a.diff_ref), lock, DOMINIO)

    destino = os.environ.get("GITHUB_STEP_SUMMARY") if a.summary else None
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Prompts: validação e prompt completo por produto\n\n" + texto + "\n")
    print(texto)
    raise SystemExit(1 if atual.erros else 0)


if __name__ == "__main__":
    main()
