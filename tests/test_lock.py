"""Lock de production: validação e resumo, num repositório Git temporário (sem MLflow)."""
import subprocess

import pytest

from mlflow_prompts_demo.lock import entrada, escrever_lock, ler_lock, resumo_mudancas, validar_lock


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    p = tmp_path / "prompts" / "atendimento"
    (p / "formato").mkdir(parents=True)
    (p / "restricoes").mkdir()
    (p / "base.md").write_text("Produto {{ produto }}.\n{{ formato }}\n{{ restricoes }}\n")
    (p / "formato" / "default.md").write_text("- curto\n")
    (p / "restricoes" / "seguro.md").write_text("- sem cláusula\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "inicial")
    monkeypatch.setenv("PROMPTS_DIR", str(tmp_path / "prompts"))
    return tmp_path


def lock_de(repo, sha):
    caminhos = ["prompts/atendimento/base.md", "prompts/atendimento/formato/default.md",
                "prompts/atendimento/restricoes/seguro.md"]
    nomes = ["atendimento-base", "atendimento-formato-default", "atendimento-restricoes-seguro"]
    return {"prompts": {n: entrada(sha, c) for n, c in zip(nomes, caminhos)}, "reprovadas": []}


def test_lock_valido_e_resumo(repo):
    sha1 = git(repo, "rev-parse", "--short", "HEAD")
    antigo = lock_de(repo, sha1)
    assert validar_lock(antigo) == []

    (repo / "prompts/atendimento/formato/default.md").write_text("- curto\n- simples\n")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "formato simples")
    sha2 = git(repo, "rev-parse", "--short", "HEAD")
    novo = dict(antigo, prompts=dict(antigo["prompts"]))
    novo["prompts"]["atendimento-formato-default"] = entrada(sha2, "prompts/atendimento/formato/default.md")

    resumo = resumo_mudancas(antigo, novo, "atendimento")
    assert "atendimento-formato-default" in resumo and "formato simples" in resumo
    assert "+- simples" in resumo


def test_lock_adulterado(repo):
    sha = git(repo, "rev-parse", "--short", "HEAD")
    lock = lock_de(repo, sha)
    lock["prompts"]["atendimento-base"]["sha256"] = "0" * 64
    lock["prompts"]["atendimento-formato-default"]["git_sha"] = "deadbee"
    erros = validar_lock(lock)
    assert any("sha256" in e for e in erros) and any("deadbee" in e for e in erros)


def test_escrever_e_ler(repo):
    sha = git(repo, "rev-parse", "--short", "HEAD")
    escrever_lock(lock_de(repo, sha))
    assert set(ler_lock()["prompts"]) == {"atendimento-base", "atendimento-formato-default", "atendimento-restricoes-seguro"}
