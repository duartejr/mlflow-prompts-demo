"""Convenções compartilhadas: nomes, tags, Git e buscas no registro.

O nome de cada prompt no MLflow vem do caminho do arquivo dentro de prompts/:

    prompts/atendimento/base.md                -> atendimento-base
    prompts/atendimento/restricoes/seguro.md   -> atendimento-restricoes-seguro
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

GERADO_POR = "prompts-ci"  # marca das versões criadas pelos scripts deste projeto


def pasta_prompts() -> Path:
    """Pasta prompts/ do repositório (sobrescreva com PROMPTS_DIR)."""
    return Path(os.environ.get("PROMPTS_DIR", "prompts")).resolve()


def raiz_repo() -> Path:
    return pasta_prompts().parent


def listar_arquivos() -> list[Path]:
    raiz = pasta_prompts()
    if not raiz.is_dir():
        raise SystemExit(f"Pasta de prompts não encontrada: {raiz}")
    return sorted(raiz.rglob("*.md"))


def caminho_relativo(arquivo: Path) -> str:
    """prompts/atendimento/base.md (relativo à raiz do repositório)."""
    return arquivo.resolve().relative_to(raiz_repo()).as_posix()


def nome_de_caminho(source_path: str) -> str:
    """'prompts/atendimento/formato/cartao.md' -> 'atendimento-formato-cartao'."""
    partes = PurePosixPath(source_path).with_suffix("").parts[1:]  # sem 'prompts/'
    return "-".join(partes)


def nome_do_prompt(arquivo: Path) -> str:
    return nome_de_caminho(caminho_relativo(arquivo))


def tags_de_caminho(source_path: str, sha: str) -> dict[str, str]:
    partes = PurePosixPath(source_path).with_suffix("").parts[1:]
    tags = {"dominio": partes[0], "source_path": source_path, "git_sha": sha, "gerado_por": GERADO_POR}
    if len(partes) >= 3:  # atendimento/restricoes/seguro
        tags["secao"], tags["produto"] = partes[1], partes[2]
    else:  # atendimento/base
        tags["secao"] = partes[-1]
    return tags


def tags_do_prompt(arquivo: Path, sha: str) -> dict[str, str]:
    return tags_de_caminho(caminho_relativo(arquivo), sha)


def git(*args: str, strip: bool = True) -> str | None:
    resultado = subprocess.run(["git", *args], capture_output=True, text=True, cwd=raiz_repo())
    if resultado.returncode != 0:
        return "" if strip else None
    return resultado.stdout.strip() if strip else resultado.stdout


def texto_no_git(sha: str, source_path: str) -> str | None:
    return git("show", f"{sha}:{source_path}", strip=False)


def ultimo_commit(source_path: str) -> str:
    return git("log", "-1", "--format=%h", "--", source_path) or git("rev-parse", "--short", "HEAD") or "sem-git"


def veio_do_git(versao) -> bool:
    """A versão do registro existe no Git com exatamente esse texto?

    Não basta olhar se há tag git_sha: uma versão criada pela interface pode ter
    tags copiadas. A prova é o próprio Git: o arquivo `source_path` no commit
    `git_sha` precisa ter o mesmo texto da versão.
    """
    sha, caminho = versao.tags.get("git_sha"), versao.tags.get("source_path")
    if not sha or not caminho:
        return False
    return texto_no_git(sha, caminho) == versao.template


def origem(versao) -> str:
    """Descrição curta de quem criou a versão, para mensagens."""
    autor = getattr(versao, "user_id", None) or "autor desconhecido"
    return f"v{versao.version} criada fora do Git ({autor}, \"{versao.commit_message or 'sem mensagem'}\")"


def carregar(uri: str, allow_missing: bool = False):
    """load_prompt SEM cache: os scripts precisam do estado real e podem alternar
    entre dois servidores no mesmo processo (o cache do MLflow não distingue servidor)."""
    import mlflow

    return mlflow.genai.load_prompt(uri, allow_missing=allow_missing, cache_ttl_seconds=0)


def carregar_versao(nome: str, versao: int | str):
    import mlflow

    return mlflow.genai.load_prompt(nome, version=versao, cache_ttl_seconds=0)


def versoes(nome: str):
    """Todas as versões de um prompt, da mais nova para a mais antiga."""
    ultima = carregar(f"prompts:/{nome}@latest", allow_missing=True)
    if ultima is None:
        return
    for v in range(int(ultima.version), 0, -1):
        yield carregar_versao(nome, v)


def versao_com_texto(nome: str, texto: str):
    """Versão existente com exatamente esse texto, ou None."""
    for versao in versoes(nome):
        if versao.template == texto:
            return versao
    return None


def prompts_no_registro(dominio: str) -> set[str]:
    import mlflow

    return {p.name for p in mlflow.genai.search_prompts(filter_string=f"name LIKE '{dominio}-%'")}


def prompts_orfaos(esperados: set[str] | None = None) -> list[str]:
    """Prompts no registro (do servidor ativo) que não estão em `esperados`.

    Por padrão, `esperados` são os arquivos de prompts/. Ex.: apagar
    prompts/atendimento/formato/cartao.md deixa atendimento-formato-cartao no
    registro (versões são imutáveis). Se o alias continuasse apontando para ele,
    o cartão nunca voltaria a usar o padrão da seção; por isso os scripts tiram
    os aliases desses prompts.
    """
    if esperados is None:
        esperados = {nome_do_prompt(a) for a in listar_arquivos()}
    dominios = {nome.split("-", 1)[0] for nome in esperados}
    no_registro: set[str] = set()
    for dominio in dominios:
        no_registro |= prompts_no_registro(dominio)
    return sorted(no_registro - esperados)
