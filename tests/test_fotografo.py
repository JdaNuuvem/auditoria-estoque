"""Testes do papel fotografo: cadastro com role, fila, upload de foto,
servir/listar fotos, zip por loja."""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AUDIT_FILE", str(tmp_path / "audit_sessions.json"))
    monkeypatch.setattr(server, "CACHE_FILE", str(tmp_path / "cache_data.json"))
    monkeypatch.setattr(server, "SEED_CACHE_FILE", str(tmp_path / "seed_cache.json"))
    monkeypatch.setattr(server, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(server, "DEDUP_FILE", str(tmp_path / "dedup_groups.json"))
    # Comentar FOTOS_FILE e FOTOS_DIR por enquanto - voltam na Task 2
    # monkeypatch.setattr(server, "FOTOS_FILE", str(tmp_path / "fotos_por_filial.json"))
    # monkeypatch.setattr(server, "FOTOS_DIR", str(tmp_path / "fotos"))
    monkeypatch.setattr(server, "_admin_login_attempts", {})
    monkeypatch.setattr(server, "_bipador_login_attempts", {})
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    server._save_users({})
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _admin():
    return {"adminPassword": "segredo123"}


def test_criar_usuario_com_role_fotografo(client):
    resp = client.post("/api/admin/bipadores", json={
        **_admin(), "name": "Foto1", "email": "foto1@x.com",
        "password": "senha123", "filialId": 1, "role": "fotografo",
    })
    assert resp.status_code == 201
    user = resp.get_json()["user"]
    assert user["role"] == "fotografo"

    saved = server._load_users()
    assert saved["foto1@x.com"]["role"] == "fotografo"


def test_criar_usuario_sem_role_vira_bipador(client):
    resp = client.post("/api/admin/bipadores", json={
        **_admin(), "name": "Bip1", "email": "bip1@x.com",
        "password": "senha123", "filialId": 1,
    })
    assert resp.status_code == 201
    assert resp.get_json()["user"]["role"] == "bipador"


def test_criar_usuario_com_role_invalido_vira_bipador(client):
    resp = client.post("/api/admin/bipadores", json={
        **_admin(), "name": "Bip2", "email": "bip2@x.com",
        "password": "senha123", "filialId": 1, "role": "gerente",
    })
    assert resp.status_code == 201
    assert resp.get_json()["user"]["role"] == "bipador"
