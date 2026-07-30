"""Testes da persistencia local (auditoria compartilhada + cache em disco).

Nao tocam a API externa nem os arquivos reais de producao: cada teste aponta
server.AUDIT_FILE / server.CACHE_FILE para um arquivo temporario via monkeypatch.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AUDIT_FILE", str(tmp_path / "audit_sessions.json"))
    monkeypatch.setattr(server, "CACHE_FILE", str(tmp_path / "cache_data.json"))
    monkeypatch.setattr(server, "USERS_FILE", str(tmp_path / "users.json"))
    server._save_users({
        "a@a.com": {"name": "A", "email": "a@a.com", "filialId": 1},
        "b@b.com": {"name": "B", "email": "b@b.com", "filialId": 2},
    })
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_audit_session_start_requires_filial_e_email(client):
    resp = client.post("/api/audit/session/start", json={"userEmail": "a@a.com"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_audit_session_start_rejeita_email_nao_cadastrado(client):
    resp = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "fantasma@a.com", "userName": "X",
    })
    assert resp.status_code == 403
    assert resp.get_json()["ok"] is False


def test_audit_fluxo_completo_start_scan_dup_finish(client):
    r = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    assert r.status_code == 201
    session = r.get_json()["session"]
    sid = session["id"]
    assert session["encontrados"] == {}

    r1 = client.post("/api/audit/scan", json={"sessionId": sid, "productId": 100, "ean": "123", "descricao": "X"})
    assert r1.get_json() == {"ok": True, "dup": False}

    r2 = client.post("/api/audit/scan", json={"sessionId": sid, "productId": 100, "ean": "123", "descricao": "X"})
    assert r2.get_json() == {"ok": True, "dup": True}

    r3 = client.post("/api/audit/session/finish", json={"sessionId": sid})
    assert r3.get_json()["session"]["fim"] != ""


def test_audit_sessions_de_usuarios_diferentes_ficam_visiveis_juntas(client):
    client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    client.post("/api/audit/session/start", json={
        "filialId": 2, "filialNome": "Loja 2", "userEmail": "b@b.com", "userName": "B",
    })
    r = client.get("/api/audit/sessions")
    sessions = r.get_json()["sessions"]
    assert len(sessions) == 2
    emails = {s["userEmail"] for s in sessions}
    assert emails == {"a@a.com", "b@b.com"}


def test_audit_scan_em_sessao_inexistente_retorna_404(client):
    r = client.post("/api/audit/scan", json={"sessionId": "nao-existe", "productId": 1})
    assert r.status_code == 404


def test_audit_persiste_em_disco_entre_chamadas(client):
    client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    assert os.path.exists(server.AUDIT_FILE)
    saved = server._load_audit()
    assert len(saved) == 1


def test_cache_save_e_load_do_disco_ida_e_volta(client):
    server.CACHE["filiais"] = [{"id": 1}]
    server.CACHE["produtos"] = [{"id": 10, "descricao": "X"}]
    server.CACHE["estoques"] = [{"idproduto": 10, "qtd": 5}]
    server.CACHE["precos"] = [{"produto": 10, "valor": 9.9}]
    server.CACHE["synced_at"] = "2026-01-01T00:00:00"
    server._save_cache_to_disk()

    server.CACHE["produtos"] = []
    server.CACHE["ready"] = False
    ok = server._load_cache_from_disk()

    assert ok is True
    assert server.CACHE["produtos"] == [{"id": 10, "descricao": "X"}]
    assert server.CACHE["ready"] is True


def test_cache_load_sem_arquivo_retorna_false(client):
    assert server._load_cache_from_disk() is False


def test_proxy_generico_da_api_nao_existe_mais(client):
    """Regressao de seguranca: o proxy aberto /api/<path> expunha toda a API i9logic
    (inclusive escrita) sem nenhuma autenticacao. Nao pode voltar a existir."""
    for method in ("get", "post", "put", "patch", "delete"):
        resp = getattr(client, method)("/api/clientes")
        assert resp.status_code == 404, f"{method.upper()} /api/clientes deveria ser 404 (rota removida)"


def test_paginated_fetch_levanta_erro_em_resposta_nao_ok(monkeypatch):
    class FakeResp:
        status_code = 429
        def json(self):
            return {"ok": False, "error": "RATE_LIMITED"}

    monkeypatch.setattr(server, "_rate_limit", lambda: None)
    monkeypatch.setattr(server.requests, "get", lambda *a, **k: FakeResp())

    with pytest.raises(RuntimeError):
        server._paginated_fetch("produtos")


def _start_session(client, filial_id=1):
    r = client.post("/api/audit/session/start", json={
        "filialId": filial_id, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    return r.get_json()["session"]["id"]


def test_audit_count_requires_session_and_product(client):
    resp = client.post("/api/audit/count", json={"delta": 1})
    assert resp.status_code == 400


def test_audit_count_exige_delta_ou_qtd(client):
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X",
    })
    assert resp.status_code == 400


def test_audit_count_rejeita_delta_e_qtd_juntos(client):
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 1, "qtd": 5,
    })
    assert resp.status_code == 400


def test_audit_count_delta_invalido_retorna_400(client):
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 5,
    })
    assert resp.status_code == 400


def test_audit_count_qtd_negativo_retorna_400(client):
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "qtd": -3,
    })
    assert resp.status_code == 400


def test_audit_count_sessao_inexistente_retorna_404(client):
    resp = client.post("/api/audit/count", json={
        "sessionId": "nao-existe", "productId": 100, "delta": 1,
    })
    assert resp.status_code == 404


def test_audit_count_increment_cria_entrada_nova(client):
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 1,
    })
    body = resp.get_json()
    assert body["ok"] is True
    assert body["qtd"] == 1
    saved = server._load_audit()[sid]
    assert saved["encontrados"]["100"]["qtd"] == 1


def test_audit_count_incrementa_e_decrementa(client):
    sid = _start_session(client)
    for _ in range(3):
        client.post("/api/audit/count", json={
            "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 1,
        })
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": -1,
    })
    assert resp.get_json()["qtd"] == 2


def test_audit_count_delta_nao_desce_abaixo_de_zero(client):
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": -1,
    })
    assert resp.get_json()["qtd"] == 0


def test_audit_count_qtd_substitui_em_vez_de_somar(client):
    sid = _start_session(client)
    client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 1,
    })
    client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 1,
    })
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "qtd": 15,
    })
    assert resp.get_json()["qtd"] == 15


def test_audit_count_calcula_qtd_sistema_e_diferenca(client):
    server.CACHE["estoques"] = [
        {"idproduto": 100, "filial": 1, "qtd": 5, "tipoestoque": 1},
        {"idproduto": 100, "filial": 1, "qtd": 3, "tipoestoque": 2},
        {"idproduto": 100, "filial": 2, "qtd": 999, "tipoestoque": 1},
    ]
    sid = _start_session(client, filial_id=1)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "qtd": 6,
    })
    body = resp.get_json()
    assert body["qtdSistema"] == 8
    assert body["diferenca"] == -2
    server.CACHE["estoques"] = []
