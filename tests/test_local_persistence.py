"""Testes da persistencia local (auditoria compartilhada + cache em disco).

Nao tocam a API externa nem os arquivos reais de producao: cada teste aponta
server.AUDIT_FILE / server.CACHE_FILE para um arquivo temporario via monkeypatch.
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AUDIT_FILE", str(tmp_path / "audit_sessions.json"))
    monkeypatch.setattr(server, "CACHE_FILE", str(tmp_path / "cache_data.json"))
    monkeypatch.setattr(server, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(server, "DEDUP_FILE", str(tmp_path / "dedup_groups.json"))
    monkeypatch.setattr(server, "_admin_login_attempts", {})
    monkeypatch.setattr(server, "_bipador_login_attempts", {})
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


def test_audit_fluxo_completo_start_scan_dup(client):
    r = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    assert r.status_code == 201
    session = r.get_json()["session"]
    sid = session["id"]
    assert session["encontrados"] == {}

    r1 = client.post("/api/audit/scan", json={"sessionId": sid, "productId": 100, "ean": "123", "descricao": "X"})
    body1 = r1.get_json()
    assert body1["ok"] is True
    assert body1["dup"] is False
    assert body1["entry"]["ean"] == "123"
    assert body1["entry"]["descricao"] == "X"

    r2 = client.post("/api/audit/scan", json={"sessionId": sid, "productId": 100, "ean": "123", "descricao": "X"})
    body2 = r2.get_json()
    assert body2["ok"] is True
    assert body2["dup"] is True
    assert body2["entry"]["ean"] == "123"


def test_audit_session_start_reusa_sessao_da_mesma_loja_no_mesmo_dia(client):
    r1 = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    assert r1.status_code == 201
    sid1 = r1.get_json()["session"]["id"]

    r2 = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "b@b.com", "userName": "B",
    })
    assert r2.status_code == 200
    sid2 = r2.get_json()["session"]["id"]

    assert sid1 == sid2
    assert len(server._load_audit()) == 1


def test_audit_session_start_cria_sessao_separada_para_loja_diferente(client):
    r1 = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    r2 = client.post("/api/audit/session/start", json={
        "filialId": 2, "filialNome": "Loja 2", "userEmail": "b@b.com", "userName": "B",
    })
    assert r1.get_json()["session"]["id"] != r2.get_json()["session"]["id"]
    assert len(server._load_audit()) == 2


def test_audit_session_finish_nao_existe_mais(client):
    r = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    sid = r.get_json()["session"]["id"]
    resp = client.post("/api/audit/session/finish", json={"sessionId": sid})
    assert resp.status_code == 404


def test_admin_login_senha_correta(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/login", json={"password": "segredo123"})
    assert resp.get_json() == {"ok": True}


def test_admin_login_senha_errada(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/login", json={"password": "errada"})
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_admin_bipadores_cria_usuario_com_senha_correta(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Carlos", "email": "carlos@x.com",
        "password": "senha123", "filialId": 3,
    })
    assert resp.status_code == 201
    assert "carlos@x.com" in server._load_users()


def test_admin_bipadores_rejeita_senha_errada(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={
        "adminPassword": "errada", "name": "Carlos", "email": "carlos@x.com",
        "password": "senha123", "filialId": 3,
    })
    assert resp.status_code == 403
    assert "carlos@x.com" not in server._load_users()


def test_admin_bipadores_exige_campos_obrigatorios(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={"adminPassword": "segredo123", "name": "Carlos"})
    assert resp.status_code == 400


def test_admin_bipadores_rejeita_email_duplicado(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    payload = {
        "adminPassword": "segredo123", "name": "Carlos", "email": "carlos@x.com",
        "password": "senha123", "filialId": 3,
    }
    client.post("/api/admin/bipadores", json=payload)
    resp = client.post("/api/admin/bipadores", json=payload)
    assert resp.status_code == 409


def test_admin_bipadores_exige_senha_com_hash_e_nunca_texto_puro(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Dora", "email": "dora@x.com",
        "password": "minhasenha", "filialId": 3,
    })
    assert resp.status_code == 201
    stored = server._load_users()["dora@x.com"]
    assert "password_hash" in stored
    assert stored["password_hash"] != "minhasenha"
    assert "password_hash" not in resp.get_json()["user"]


def test_admin_bipadores_rejeita_senha_curta(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Eva", "email": "eva@x.com",
        "password": "123", "filialId": 3,
    })
    assert resp.status_code == 400


def test_admin_bipadores_rejeita_senha_ausente(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Fabio", "email": "fabio@x.com", "filialId": 3,
    })
    assert resp.status_code == 400


def test_login_com_senha_correta_funciona(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Gustavo", "email": "gustavo@x.com",
        "password": "senhacerta", "filialId": 3,
    })
    resp = client.post("/api/auth/login", json={"email": "gustavo@x.com", "password": "senhacerta"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "password_hash" not in body["user"]


def test_login_com_senha_errada_e_rejeitado(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Helena", "email": "helena@x.com",
        "password": "senhacerta", "filialId": 3,
    })
    resp = client.post("/api/auth/login", json={"email": "helena@x.com", "password": "senhaerrada"})
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_login_de_conta_legada_sem_password_hash_e_rejeitado(client):
    resp = client.post("/api/auth/login", json={"email": "a@a.com", "password": "qualquer"})
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_list_users_nunca_devolve_password_hash(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Igor", "email": "igor@x.com",
        "password": "senha123", "filialId": 3,
    })
    resp = client.get("/api/auth/users")
    users = resp.get_json()["users"]
    assert len(users) > 0
    assert all("password_hash" not in u for u in users)


def test_reset_password_funciona_e_senha_antiga_deixa_de_funcionar(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Julia", "email": "julia@x.com",
        "password": "senhaA1", "filialId": 3,
    })
    resp = client.post("/api/admin/bipadores/reset-password", json={
        "adminPassword": "segredo123", "email": "julia@x.com", "password": "senhaB2",
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    old_login = client.post("/api/auth/login", json={"email": "julia@x.com", "password": "senhaA1"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"email": "julia@x.com", "password": "senhaB2"})
    assert new_login.status_code == 200


def test_reset_password_rejeita_senha_admin_errada(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Kleber", "email": "kleber@x.com",
        "password": "senhaOriginal", "filialId": 3,
    })
    resp = client.post("/api/admin/bipadores/reset-password", json={
        "adminPassword": "errada", "email": "kleber@x.com", "password": "senhaNova1",
    })
    assert resp.status_code == 403

    old_login = client.post("/api/auth/login", json={"email": "kleber@x.com", "password": "senhaOriginal"})
    assert old_login.status_code == 200


def test_reset_password_rejeita_bipador_inexistente(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores/reset-password", json={
        "adminPassword": "segredo123", "email": "fantasma@x.com", "password": "senhaNova1",
    })
    assert resp.status_code == 404


def test_reset_password_rejeita_senha_curta(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    client.post("/api/admin/bipadores", json={
        "adminPassword": "segredo123", "name": "Nina", "email": "nina@x.com",
        "password": "senhaOriginal", "filialId": 3,
    })
    resp = client.post("/api/admin/bipadores/reset-password", json={
        "adminPassword": "segredo123", "email": "nina@x.com", "password": "123",
    })
    assert resp.status_code == 400

    old_login = client.post("/api/auth/login", json={"email": "nina@x.com", "password": "senhaOriginal"})
    assert old_login.status_code == 200


def test_auth_register_nao_existe_mais(client):
    resp = client.post("/api/auth/register", json={"name": "X", "email": "x@x.com", "filialId": 1})
    assert resp.status_code == 404


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


def test_audit_count_qtd_sistema_disponivel_false_quando_cache_vazio(client):
    server.CACHE["estoques"] = []
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 1,
    })
    assert resp.get_json()["qtdSistemaDisponivel"] is False


def test_audit_count_qtd_sistema_disponivel_true_quando_cache_populado(client):
    server.CACHE["estoques"] = [{"idproduto": 100, "filial": 1, "qtd": 5, "tipoestoque": 1}]
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 100, "ean": "1", "descricao": "X", "delta": 1,
    })
    assert resp.get_json()["qtdSistemaDisponivel"] is True
    server.CACHE["estoques"] = []


def test_audit_count_rejeita_productId_nao_numerico(client):
    sid = _start_session(client)
    resp = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": "abc", "ean": "1", "descricao": "X", "delta": 1,
    })
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "productId deve ser um numero."
    saved = server._load_audit()[sid]
    assert "abc" not in saved["encontrados"]


def test_audit_session_start_nao_reaproveita_sessao_de_ontem(client):
    ontem = (date.today() - timedelta(days=1)).isoformat()
    sessao_ontem = {
        "id": "audit_1_ontem_000", "userEmail": "a@a.com", "userName": "A",
        "filialId": 1, "filialNome": "Loja 1", "data": ontem,
        "inicio": "08:00:00", "encontrados": {},
    }
    server._save_audit({sessao_ontem["id"]: sessao_ontem})

    resp = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    assert resp.status_code == 201
    nova_sessao = resp.get_json()["session"]
    assert nova_sessao["id"] != sessao_ontem["id"]
    assert len(server._load_audit()) == 2


def test_audit_session_start_reaproveitamento_preserva_produtos_bipados(client):
    r1 = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    assert r1.status_code == 201
    sid = r1.get_json()["session"]["id"]

    client.post("/api/audit/scan", json={"sessionId": sid, "productId": 100, "ean": "123", "descricao": "X"})

    r2 = client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja 1", "userEmail": "a@a.com", "userName": "A",
    })
    assert r2.status_code == 200
    sessao = r2.get_json()["session"]
    assert "100" in sessao["encontrados"]


def test_admin_login_senha_ausente_ou_vazia_e_rejeitada(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp1 = client.post("/api/admin/login", json={})
    assert resp1.status_code == 401
    assert resp1.get_json()["ok"] is False

    resp2 = client.post("/api/admin/login", json={"password": ""})
    assert resp2.status_code == 401
    assert resp2.get_json()["ok"] is False


def test_normalize_descricao_remove_acentos_maiusculas_espacos_extras():
    assert server._normalize_descricao("  Shampoo   REPARAÇÃO Intensa  ") == "shampoo reparacao intensa"


def test_normalize_descricao_lida_com_none_e_vazio():
    assert server._normalize_descricao(None) == ""
    assert server._normalize_descricao("") == ""


def test_group_signature_ordena_ids_numericamente():
    assert server._group_signature([456, 123]) == "123-456"
    assert server._group_signature([10, 2]) == "2-10"


def test_signature_to_ids_reverte_group_signature():
    assert server._signature_to_ids("123-456") == [123, 456]


def test_signature_to_ids_rejeita_formato_invalido():
    with pytest.raises(ValueError):
        server._signature_to_ids("abc-123")


def test_load_dedup_arquivo_inexistente_retorna_dict_vazio(client):
    assert server._load_dedup() == {}


def test_save_e_load_dedup_faz_roundtrip(client):
    data = {"1": {"123-456": {"status": "approved", "memberProductIds": [123, 456], "canonicalProductId": 123}}}
    server._save_dedup(data)
    assert server._load_dedup() == data
