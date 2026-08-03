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


def _set_dedup_catalog(produtos, estoques):
    server.CACHE["produtos"] = produtos
    server.CACHE["estoques"] = estoques


def test_find_duplicate_candidates_detecta_ean_identico(client):
    _set_dedup_catalog(
        produtos=[
            {"id": 1, "descricao": "Batom Vermelho", "ean": "789000000001", "ncm": "3304", "marca": "X", "codproduto": "A1"},
            {"id": 2, "descricao": "Batom Vermelho Intenso", "ean": "789000000001", "ncm": "3305", "marca": "Y", "codproduto": "A2"},
        ],
        estoques=[
            {"idproduto": 1, "filial": 1, "qtd": 3},
            {"idproduto": 2, "filial": 1, "qtd": 7},
        ],
    )
    candidates = server._find_duplicate_candidates(1)
    assert len(candidates) == 1
    assert candidates[0]["confidence"] == "alta"
    assert "ean_igual" in candidates[0]["signals"]
    assert candidates[0]["memberProductIds"] == [1, 2]
    assert candidates[0]["signature"] == "1-2"
    estoques_por_id = {m["id"]: m["estoqueFilial"] for m in candidates[0]["members"]}
    assert estoques_por_id == {1: 3, 2: 7}
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_find_duplicate_candidates_detecta_ncm_marca_descricao_similar(client):
    _set_dedup_catalog(
        produtos=[
            {"id": 10, "descricao": "Shampoo Reparação Intensa 500ml", "ean": "1", "ncm": "3305", "marca": "Beleza", "codproduto": "S1"},
            {"id": 11, "descricao": "Shampoo Reparacao Intensa 500 ml", "ean": "2", "ncm": "3305", "marca": "Beleza", "codproduto": "S2"},
        ],
        estoques=[
            {"idproduto": 10, "filial": 1, "qtd": 1},
            {"idproduto": 11, "filial": 1, "qtd": 1},
        ],
    )
    candidates = server._find_duplicate_candidates(1)
    assert len(candidates) == 1
    assert candidates[0]["confidence"] in ("alta", "media")
    assert "ncm_marca_iguais" in candidates[0]["signals"]
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_find_duplicate_candidates_nao_agrupa_produtos_diferentes(client):
    _set_dedup_catalog(
        produtos=[
            {"id": 20, "descricao": "Batom Vermelho", "ean": "1", "ncm": "3304", "marca": "X", "codproduto": "B1"},
            {"id": 21, "descricao": "Esmalte Azul Metalico", "ean": "2", "ncm": "3305", "marca": "Y", "codproduto": "B2"},
        ],
        estoques=[
            {"idproduto": 20, "filial": 1, "qtd": 1},
            {"idproduto": 21, "filial": 1, "qtd": 1},
        ],
    )
    candidates = server._find_duplicate_candidates(1)
    assert candidates == []
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_find_duplicate_candidates_ignora_produto_sem_estoque_na_filial(client):
    _set_dedup_catalog(
        produtos=[
            {"id": 30, "descricao": "Batom Vermelho", "ean": "1", "ncm": "3304", "marca": "X", "codproduto": "C1"},
            {"id": 31, "descricao": "Batom Vermelho", "ean": "1", "ncm": "3304", "marca": "X", "codproduto": "C2"},
        ],
        estoques=[
            {"idproduto": 30, "filial": 1, "qtd": 1},
            {"idproduto": 31, "filial": 2, "qtd": 1},
        ],
    )
    candidates_filial_1 = server._find_duplicate_candidates(1)
    assert candidates_filial_1 == []
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_find_duplicate_candidates_exclui_signature_ja_decidida(client):
    _set_dedup_catalog(
        produtos=[
            {"id": 40, "descricao": "Batom Vermelho", "ean": "1", "ncm": "3304", "marca": "X", "codproduto": "D1"},
            {"id": 41, "descricao": "Batom Vermelho", "ean": "1", "ncm": "3304", "marca": "X", "codproduto": "D2"},
        ],
        estoques=[
            {"idproduto": 40, "filial": 1, "qtd": 1},
            {"idproduto": 41, "filial": 1, "qtd": 1},
        ],
    )
    server._save_dedup({"1": {"40-41": {"status": "rejected", "memberProductIds": [40, 41]}}})
    candidates = server._find_duplicate_candidates(1)
    assert candidates == []
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_find_duplicate_candidates_ean_igual_sem_semelhanca_de_descricao_nao_vira_alta(client):
    """Regressao com dado real de producao: no catalogo da CHARME COSMETICOS ha
    colisoes reais e pontuais de EAN (nao o placeholder "SEM GTIN", ja tratado)
    entre produtos completamente sem relacao - ex.: "MINI VENTILADOR REFHW-2" e
    "COLA PARA CILIOS 0.5S 10 ML SUNNYS-REFSS8801" com o mesmo EAN 7000000091148.
    O sinal ean_igual continua verdadeiro, mas o score efetivo do par so vira 100
    se a descricao tiver ao menos uma semelhanca minima (piso de 30); abaixo disso,
    o par usa o score real, rebaixando a confianca do grupo para "baixa" - evitando
    que caia no lote de aprovacao automatica de "alta" confianca."""
    _set_dedup_catalog(
        produtos=[
            {"id": 70, "descricao": "MINI VENTILADOR REFHW-2", "ean": "7000000091148", "ncm": "8414", "marca": "Refresca", "codproduto": "H1"},
            {"id": 71, "descricao": "COLA PARA CILIOS 0.5S 10 ML SUNNYS-REFSS8801", "ean": "7000000091148", "ncm": "3506", "marca": "Sunnys", "codproduto": "H2"},
        ],
        estoques=[
            {"idproduto": 70, "filial": 1, "qtd": 1},
            {"idproduto": 71, "filial": 1, "qtd": 1},
        ],
    )
    candidates = server._find_duplicate_candidates(1)
    assert len(candidates) == 1
    assert "ean_igual" in candidates[0]["signals"]
    assert candidates[0]["confidence"] == "baixa"
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_find_duplicate_candidates_grupo_de_tres_confianca_usa_o_pior_par(client):
    """Regressao: produto 50-51 tem EAN igual (par forte), mas 51-52 so se une por
    ncm+marca com similaridade fraca de descricao (~63%, abaixo de 75). A confianca
    do grupo final {50,51,52} deve refletir o PIOR par, nao o melhor - ou seja, nao
    pode sair "alta" so porque um dos pares tem EAN identico."""
    _set_dedup_catalog(
        produtos=[
            {"id": 50, "descricao": "Vela Aromatica Lavanda", "ean": "111", "ncm": "9000", "marca": "Aroma", "codproduto": "F1"},
            {"id": 51, "descricao": "Perfume Floral Doce 100ml", "ean": "111", "ncm": "3303", "marca": "Aroma", "codproduto": "F2"},
            {"id": 52, "descricao": "Perfume Amadeirado Intenso 100ml", "ean": "222", "ncm": "3303", "marca": "Aroma", "codproduto": "F3"},
        ],
        estoques=[
            {"idproduto": 50, "filial": 1, "qtd": 1},
            {"idproduto": 51, "filial": 1, "qtd": 1},
            {"idproduto": 52, "filial": 1, "qtd": 1},
        ],
    )
    candidates = server._find_duplicate_candidates(1)
    assert len(candidates) == 1
    assert candidates[0]["memberProductIds"] == [50, 51, 52]
    assert candidates[0]["signature"] == "50-51-52"
    assert "ean_igual" in candidates[0]["signals"]
    assert "ncm_marca_iguais" in candidates[0]["signals"]
    assert candidates[0]["confidence"] != "alta"
    assert candidates[0]["confidence"] == "baixa"
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_find_duplicate_candidates_ignora_ean_placeholder_sem_gtin(client):
    """Regressao com dado real de producao: o i9logic usa o literal "SEM GTIN" no
    campo ean para produtos sem codigo de barras real. Tratar isso como EAN valido
    faria o bucketing agrupar QUALQUER produto sem GTIN como duplicata de outro
    (mega-grupo com centenas de produtos nao relacionados). A checagem precisa ser
    case-insensitive ("SEM GTIN" e "sem gtin" sao o mesmo placeholder)."""
    _set_dedup_catalog(
        produtos=[
            {"id": 60, "descricao": "Caneca Termica Aco", "ean": "SEM GTIN", "ncm": "7323", "marca": "CasaX", "codproduto": "G1"},
            {"id": 61, "descricao": "Espatula Silicone Cozinha", "ean": "sem gtin", "ncm": "8205", "marca": "CasaY", "codproduto": "G2"},
            {"id": 62, "descricao": "Cilios Posticos Volume", "ean": "SEM GTIN", "ncm": "9615", "marca": "BelezaZ", "codproduto": "G3"},
        ],
        estoques=[
            {"idproduto": 60, "filial": 1, "qtd": 1},
            {"idproduto": 61, "filial": 1, "qtd": 1},
            {"idproduto": 62, "filial": 1, "qtd": 1},
        ],
    )
    candidates = server._find_duplicate_candidates(1)
    assert candidates == []
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_dedup_analyze_exige_senha_admin_correta(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/dedup/analyze", json={"adminPassword": "errada", "filialId": 1})
    assert resp.status_code == 403


def test_dedup_analyze_retorna_candidatos(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    _set_dedup_catalog(
        produtos=[
            {"id": 50, "descricao": "Batom Vermelho", "ean": "1", "ncm": "3304", "marca": "X", "codproduto": "E1"},
            {"id": 51, "descricao": "Batom Vermelho", "ean": "1", "ncm": "3304", "marca": "X", "codproduto": "E2"},
        ],
        estoques=[
            {"idproduto": 50, "filial": 1, "qtd": 1},
            {"idproduto": 51, "filial": 1, "qtd": 1},
        ],
    )
    resp = client.post("/api/admin/dedup/analyze", json={"adminPassword": "segredo123", "filialId": 1})
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["signature"] == "50-51"
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []


def test_dedup_confirm_persiste_grupo_aprovado(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/dedup/confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": "60-61", "canonicalProductId": 60,
    })
    assert resp.status_code == 200
    saved = server._load_dedup()
    assert saved["1"]["60-61"]["status"] == "approved"
    assert saved["1"]["60-61"]["canonicalProductId"] == 60
    assert saved["1"]["60-61"]["memberProductIds"] == [60, 61]


def test_dedup_confirm_rejeita_canonico_fora_do_grupo(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/dedup/confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": "60-61", "canonicalProductId": 999,
    })
    assert resp.status_code == 400


def test_dedup_bulk_confirm_escolhe_canonico_por_maior_estoque(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    server.CACHE["estoques"] = [
        {"idproduto": 70, "filial": 1, "qtd": 3},
        {"idproduto": 71, "filial": 1, "qtd": 9},
    ]
    resp = client.post("/api/admin/dedup/bulk-confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signatures": ["70-71"],
    })
    assert resp.status_code == 200
    saved = server._load_dedup()
    assert saved["1"]["70-71"]["canonicalProductId"] == 71
    server.CACHE["estoques"] = []


def test_dedup_reject_persiste_grupo_rejeitado(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/dedup/reject", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": "80-81",
    })
    assert resp.status_code == 200
    saved = server._load_dedup()
    assert saved["1"]["80-81"]["status"] == "rejected"


def test_dedup_decisoes_sao_isoladas_por_filial(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    client.post("/api/admin/dedup/confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": "90-91", "canonicalProductId": 90,
    })
    saved = server._load_dedup()
    assert "1" in saved and "90-91" in saved["1"]
    assert "2" not in saved


def test_dedup_groups_devolve_so_aprovados_da_filial_pedida(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    server.CACHE["produtos"] = [
        {"id": 100, "descricao": "Produto A", "ean": "1", "codproduto": "F1"},
        {"id": 101, "descricao": "Produto A Variante", "ean": "2", "codproduto": "F2"},
    ]
    client.post("/api/admin/dedup/confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": "100-101", "canonicalProductId": 100,
    })
    client.post("/api/admin/dedup/reject", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": "200-201",
    })
    resp = client.get("/api/dedup/groups?filialId=1")
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["groups"]) == 1
    assert body["groups"][0]["canonicalProductId"] == 100
    assert len(body["groups"][0]["members"]) == 2

    resp_outra_filial = client.get("/api/dedup/groups?filialId=2")
    assert resp_outra_filial.get_json()["groups"] == []
    server.CACHE["produtos"] = []


def test_dedup_confirm_com_signature_nao_string_retorna_400(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp_lista = client.post("/api/admin/dedup/confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": [70, 71], "canonicalProductId": 70,
    })
    assert resp_lista.status_code == 400
    resp_int = client.post("/api/admin/dedup/confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": 123, "canonicalProductId": 123,
    })
    assert resp_int.status_code == 400


def test_dedup_reject_com_signature_nao_string_retorna_400(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/dedup/reject", json={
        "adminPassword": "segredo123", "filialId": 1, "signature": [80, 81],
    })
    assert resp.status_code == 400


def test_dedup_bulk_confirm_ignora_item_invalido_na_lista_de_signatures(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    server.CACHE["estoques"] = [
        {"idproduto": 70, "filial": 1, "qtd": 3},
        {"idproduto": 71, "filial": 1, "qtd": 9},
        {"idproduto": 72, "filial": 1, "qtd": 1},
        {"idproduto": 73, "filial": 1, "qtd": 5},
    ]
    resp = client.post("/api/admin/dedup/bulk-confirm", json={
        "adminPassword": "segredo123", "filialId": 1, "signatures": ["70-71", 999, "72-73"],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["confirmed"] == ["70-71", "72-73"]
    saved = server._load_dedup()
    assert "70-71" in saved["1"]
    assert "72-73" in saved["1"]
    assert saved["1"]["70-71"]["canonicalProductId"] == 71
    assert saved["1"]["72-73"]["canonicalProductId"] == 73
    server.CACHE["estoques"] = []
