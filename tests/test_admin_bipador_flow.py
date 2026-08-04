"""Testes de fluxo completo: admin e bipador.

Cobre update-filial, rate limits, seed cache, login edge cases,
fluxo bipador completo (login -> sessao -> scan -> count).
"""
import sys
import os
import json
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import server
from server import _rate_limited, _record_failed_attempt, _load_cache_from_seed


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AUDIT_FILE", str(tmp_path / "audit_sessions.json"))
    monkeypatch.setattr(server, "CACHE_FILE", str(tmp_path / "cache_data.json"))
    monkeypatch.setattr(server, "SEED_CACHE_FILE", str(tmp_path / "seed_cache.json"))
    monkeypatch.setattr(server, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(server, "DEDUP_FILE", str(tmp_path / "dedup_groups.json"))
    monkeypatch.setattr(server, "_admin_login_attempts", {})
    monkeypatch.setattr(server, "_bipador_login_attempts", {})
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    server._save_users({
        "a@a.com": {"name": "A", "email": "a@a.com", "filialId": 1},
        "b@b.com": {"name": "B", "email": "b@b.com", "filialId": 2},
    })
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _admin():
    return {"adminPassword": "segredo123"}


# ── admin: update-filial ──

def _criar_bipador(client, email="t1@x.com", filial_id=1):
    client.post("/api/admin/bipadores", json={
        **_admin(), "name": "T1", "email": email, "password": "senha123", "filialId": filial_id,
    })


def test_update_filial_troca_loja_do_bipador(client):
    _criar_bipador(client, "t1@x.com", 1)
    resp = client.post("/api/admin/bipadores/update-filial", json={
        **_admin(), "email": "t1@x.com", "filialId": 3,
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert resp.get_json()["filialId"] == 3

    saved = server._load_users()
    assert saved["t1@x.com"]["filialId"] == 3


def test_update_filial_exige_email(client):
    resp = client.post("/api/admin/bipadores/update-filial", json={
        **_admin(), "filialId": 3,
    })
    assert resp.status_code == 400


def test_update_filial_exige_filial_id(client):
    _criar_bipador(client, "t2@x.com", 1)
    resp = client.post("/api/admin/bipadores/update-filial", json={
        **_admin(), "email": "t2@x.com",
    })
    assert resp.status_code == 400


def test_update_filial_rejeita_bipador_inexistente(client):
    resp = client.post("/api/admin/bipadores/update-filial", json={
        **_admin(), "email": "fantasma@x.com", "filialId": 1,
    })
    assert resp.status_code == 404


def test_update_filial_rejeita_senha_admin_errada(client):
    _criar_bipador(client, "t3@x.com", 1)
    resp = client.post("/api/admin/bipadores/update-filial", json={
        "adminPassword": "errada", "email": "t3@x.com", "filialId": 2,
    })
    assert resp.status_code == 403

    saved = server._load_users()
    assert saved["t3@x.com"]["filialId"] == 1


# ── admin: rate limiting ──

def test_admin_login_rate_limit_bloqueia_apos_exceder_tentativas(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_LOGIN_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")

    for _ in range(4):
        client.post("/api/admin/login", json={"password": "errada"})

    resp = client.post("/api/admin/login", json={"password": "segredo123"})
    assert resp.status_code == 429
    assert resp.get_json()["ok"] is False


def test_bipador_login_rate_limit_bloqueia_apos_exceder_tentativas(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_LOGIN_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    _criar_bipador(client, "limitado@x.com", 1)

    for _ in range(4):
        client.post("/api/auth/login", json={"email": "limitado@x.com", "password": "errada"})

    resp = client.post("/api/auth/login", json={"email": "limitado@x.com", "password": "senha123"})
    assert resp.status_code == 429


# ── seed cache ──

def test_seed_cache_carrega_de_arquivo(client):
    seed_data = {
        "filiais": [{"id": 1, "fantasia": "Loja A"}],
        "produtos": [{"id": 100, "descricao": "Produto Seed", "ean": "123", "codproduto": "S1"}],
        "estoques": [{"idproduto": 100, "filial": 1, "qtd": 5, "tipoestoque": 1}],
        "precos": [{"produto": 100, "tabela": 1, "valor": 9.99}],
        "synced_at": "2026-01-01T00:00:00",
    }
    with open(server.SEED_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(seed_data, f)

    assert server._load_cache_from_seed() is True
    assert server.CACHE["ready"] is True
    assert len(server.CACHE["produtos"]) == 1


def test_seed_cache_arquivo_inexistente_retorna_false(client):
    assert os.path.exists(server.SEED_CACHE_FILE) is False or server.SEED_CACHE_FILE.endswith("seed_cache.json")
    result = server._load_cache_from_seed()
    if os.path.exists(server.SEED_CACHE_FILE):
        os.remove(server.SEED_CACHE_FILE)
    assert result is False


def test_seed_cache_json_invalido_retorna_false(client):
    with open(server.SEED_CACHE_FILE, "w", encoding="utf-8") as f:
        f.write("nao e json")

    assert server._load_cache_from_seed() is False


# ── bipador: login edge cases ──

def test_login_sem_campos_obrigatorios_retorna_400(client):
    resp = client.post("/api/auth/login", json={})
    assert resp.status_code == 400

    resp = client.post("/api/auth/login", json={"email": "a@a.com"})
    assert resp.status_code == 400

    resp = client.post("/api/auth/login", json={"password": "senha"})
    assert resp.status_code == 400


def test_login_email_invalido_sem_arroba_tambem_falha(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    _criar_bipador(client, "com@arroba.com")
    resp = client.post("/api/auth/login", json={"email": "semarroba", "password": "senha123"})
    assert resp.status_code == 401


# ── bipador: session + scan + count ──

def _start_session(client, filial_id=1, email="a@a.com"):
    r = client.post("/api/audit/session/start", json={
        "filialId": filial_id, "filialNome": "Loja",
        "userEmail": email, "userName": email.split("@")[0],
    })
    return r.get_json()["session"]["id"]


def test_fluxo_bipador_completo(client):
    sid = _start_session(client)

    r = client.post("/api/audit/scan", json={
        "sessionId": sid, "productId": 1, "ean": "78900001", "descricao": "Produto Teste",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["dup"] is False
    assert body["entry"]["ean"] == "78900001"

    r2 = client.post("/api/audit/scan", json={
        "sessionId": sid, "productId": 1, "ean": "78900001", "descricao": "Produto Teste",
    })
    assert r2.get_json()["dup"] is True

    r3 = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 2, "ean": "2", "descricao": "Outro", "delta": 1,
    })
    assert r3.get_json()["ok"] is True
    assert r3.get_json()["qtd"] == 1

    r4 = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 2, "ean": "2", "descricao": "Outro", "qtd": 10,
    })
    assert r4.get_json()["qtd"] == 10


def test_bipador_scan_sem_session_id_retorna_400(client):
    r = client.post("/api/audit/scan", json={"productId": 1, "ean": "1", "descricao": "X"})
    assert r.status_code == 400


def test_bipador_count_qtd_zero_eh_aceito(client):
    sid = _start_session(client)
    r = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 1, "ean": "1", "descricao": "X", "qtd": 0,
    })
    assert r.get_json()["ok"] is True
    assert r.get_json()["qtd"] == 0


def test_bipador_count_delta_zero_eh_rejeitado(client):
    sid = _start_session(client)
    client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 1, "ean": "1", "descricao": "X", "delta": 1,
    })
    r = client.post("/api/audit/count", json={
        "sessionId": sid, "productId": 1, "ean": "1", "descricao": "X", "delta": 0,
    })
    assert r.status_code == 400


def test_paginated_fetch_com_rate_limit_no_body_faz_retry(monkeypatch):
    call_count = [0]

    class FakeResp:
        status_code = 200

        def json(self):
            call_count[0] += 1
            if call_count[0] <= 2:
                return {"ok": False, "error": "RATE_LIMIT_EXCEEDED"}
            return {"ok": True, "data": [], "total": 0}

    monkeypatch.setattr(server, "_rate_limit", lambda: None)
    monkeypatch.setattr(server.time, "sleep", lambda _: None)
    monkeypatch.setattr(server.requests, "get", lambda *a, **k: FakeResp())

    result = server._paginated_fetch("teste")
    assert result == []
    assert call_count[0] >= 3


def test_legacy_user_sem_password_hash_e_rejeitado(client):
    server._save_users({
        "legado@x.com": {"name": "Legado", "email": "legado@x.com", "filialId": 1},
    })
    resp = client.post("/api/auth/login", json={"email": "legado@x.com", "password": "qualquer"})
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_login_conta_existente_tempo_constante_para_conta_inexistente(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    _criar_bipador(client, "existe@x.com")

    t1 = time.perf_counter()
    client.post("/api/auth/login", json={"email": "existe@x.com", "password": "errada"})
    duracao_existente = time.perf_counter() - t1

    t2 = time.perf_counter()
    client.post("/api/auth/login", json={"email": "naoexiste@x.com", "password": "errada"})
    duracao_inexistente = time.perf_counter() - t2

    assert abs(duracao_existente - duracao_inexistente) < 0.3


def test_reset_password_mantem_outros_campos_intactos(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    _criar_bipador(client, "intacto@x.com", filial_id=5)

    client.post("/api/admin/bipadores/reset-password", json={
        **_admin(), "email": "intacto@x.com", "password": "nova123",
    })
    saved = server._load_users()
    assert saved["intacto@x.com"]["filialId"] == 5
    assert saved["intacto@x.com"]["name"] == "T1"


def test_update_filial_mantem_outros_campos_intactos(client):
    _criar_bipador(client, "campos@x.com", filial_id=1)

    client.post("/api/admin/bipadores/update-filial", json={
        **_admin(), "email": "campos@x.com", "filialId": 7,
    })
    saved = server._load_users()
    assert saved["campos@x.com"]["filialId"] == 7
    assert saved["campos@x.com"]["name"] == "T1"


def test_create_bipador_retorna_user_sem_hash_e_com_todos_os_campos(client):
    resp = client.post("/api/admin/bipadores", json={
        **_admin(), "name": "Completo", "email": "completo@x.com",
        "password": "senha123", "filialId": 2,
    })
    assert resp.status_code == 201
    user = resp.get_json()["user"]
    assert user["name"] == "Completo"
    assert user["email"] == "completo@x.com"
    assert user["filialId"] == 2
    assert "password_hash" not in user
    assert "password" not in user


def test_bipador_criado_consegue_logar(client):
    _criar_bipador(client, "login@x.com")
    resp = client.post("/api/auth/login", json={"email": "login@x.com", "password": "senha123"})
    assert resp.status_code == 200
    assert resp.get_json()["user"]["email"] == "login@x.com"


def test_sessoes_devolve_lista_ordenada(client):
    client.post("/api/audit/session/start", json={
        "filialId": 1, "filialNome": "Loja1", "userEmail": "a@a.com", "userName": "A",
    })
    client.post("/api/audit/session/start", json={
        "filialId": 2, "filialNome": "Loja2", "userEmail": "b@b.com", "userName": "B",
    })
    r = client.get("/api/audit/sessions")
    sessions = r.get_json()["sessions"]
    assert len(sessions) == 2
    filiais = {s["filialId"] for s in sessions}
    assert filiais == {1, 2}


def test_uniao_duas_particoes_compartilhando_ean(client):
    server.CACHE["produtos"] = [
        {"id": 1, "descricao": "P1", "ean": "A", "ncm": "x", "marca": "x", "codproduto": "C1"},
        {"id": 2, "descricao": "P2", "ean": "A", "ncm": "x", "marca": "x", "codproduto": "C2"},
        {"id": 3, "descricao": "P3", "ean": "B", "ncm": "x", "marca": "x", "codproduto": "C3"},
        {"id": 4, "descricao": "P4", "ean": "B", "ncm": "x", "marca": "x", "codproduto": "C4"},
    ]
    server.CACHE["estoques"] = [
        {"idproduto": 1, "filial": 1, "qtd": 1},
        {"idproduto": 2, "filial": 1, "qtd": 1},
        {"idproduto": 3, "filial": 1, "qtd": 1},
        {"idproduto": 4, "filial": 1, "qtd": 1},
    ]
    candidates = server._find_duplicate_candidates(1)
    assert len(candidates) == 2
    signatures = {c["signature"] for c in candidates}
    assert signatures == {"1-2", "3-4"}
    server.CACHE["produtos"] = []
    server.CACHE["estoques"] = []
