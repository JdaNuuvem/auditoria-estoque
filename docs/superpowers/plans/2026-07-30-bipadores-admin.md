# Bipadores por Loja + Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sessão de auditoria compartilhada por loja+dia, cadastro de bipador restrito a um admin único (aba nova), e um fluxo de bipagem unificado onde o 1º bipe de um produto confirma existência e o 2º em diante conta unidades — sem alternância manual de modo.

**Architecture:** Backend: `audit_session_start` passa a reaproveitar a sessão do dia daquela loja em vez de criar sempre uma nova; dois endpoints novos (`/api/admin/login`, `/api/admin/bipadores`) substituem o autocadastro aberto; `/api/audit/session/finish` é removido (sem uso depois que a sessão passa a ser compartilhada). Frontend: uma função única (`registerHit`) decide, olhando o estado local da sessão, se chama o fluxo de "existe" (`/api/audit/scan`, já existe) ou o de "conta" (`/api/audit/count`, já existe) — nenhum dos dois endpoints de contagem/verificação muda. Três sub-abas (Bipagem/Produtos Existentes/Estoque Contado) substituem as duas atuais (Verificação/Contagem). Um novo modo "admin" (senha única) esconde a navegação normal e mostra só a aba Bipadores.

**Tech Stack:** Flask (Python) + vanilla JS/HTML (sem build step, sem framework, sem harness de teste JS — igual ao resto do projeto). pytest + Flask test client para o backend.

## Global Constraints

- `ADMIN_PASSWORD` já está configurada no `.env` local (e um placeholder em `.env.example`) — **não tente ler, editar ou imprimir esses arquivos**: `.env*` é bloqueado para leitura/edição neste ambiente por segurança. O backend lê a variável via `os.environ["ADMIN_PASSWORD"]` (falha rápido se ausente, mesmo padrão de `I9LOGIC_CLIENT_ID`/`I9LOGIC_TOKEN` já existente em `server.py`).
- Nenhuma dependência nova (nem Python nem JS).
- `/api/audit/scan` e `/api/audit/count` **não mudam** — a regra "1º bipe existe, 2º+ conta" é inteiramente uma decisão do front-end sobre qual dos dois chamar.
- Bipagem física (leitor USB/teclado, câmera, imagem) resolve **sempre por EAN exato** — isso não muda.
- Busca manual continua resolvendo por EAN → SKU (`codproduto`) → lista de candidatos por NCM — isso não muda, só a ação final sobre o produto resolvido passa a seguir a regra "existe/conta".
- Comparação de senha usa `hmac.compare_digest` (tempo constante), não `==`.
- Depois deste plano implementado, ainda falta (fora do escopo das tarefas abaixo, ação manual do usuário): adicionar `ADMIN_PASSWORD` nas variáveis de ambiente do Coolify antes do próximo deploy — sem isso o servidor de produção não sobe.
- Spec completo: `docs/superpowers/specs/2026-07-30-bipadores-admin-design.md`.

---

### Task 1: Backend — admin, sessão por loja+dia, remoção do autocadastro/finish

**Files:**
- Modify: `server.py`
- Modify: `tests/test_local_persistence.py`

**Interfaces:**
- Consumes: `_load_users()`, `_save_users()`, `_load_audit()`, `_save_audit()`, `_audit_lock` (já existem em `server.py`).
- Produces: rota `POST /api/admin/login` (body `{password}` → `{ok}`), rota `POST /api/admin/bipadores` (body `{adminPassword, name, email, filialId}` → mesmo contrato que o antigo `/api/auth/register`, mais 403 se `adminPassword` não bater), `audit_session_start` devolvendo uma sessão existente (200) em vez de sempre criar (201) quando já existe uma sessão com o mesmo `filialId` e a mesma data. Nenhuma outra tarefa depende de nomes novos além dessas duas rotas e desse comportamento — é a última tarefa que mexe em `server.py`.

- [ ] **Step 1: Escrever os testes (falhando)**

Abra `tests/test_local_persistence.py`. Primeiro, localize e **substitua** o teste existente que referencia o endpoint `/api/audit/session/finish` (que está sendo removido nesta tarefa):

Find:
```python
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
```

Replace:
```python
def test_audit_fluxo_completo_start_scan_dup(client):
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
        "adminPassword": "segredo123", "name": "Carlos", "email": "carlos@x.com", "filialId": 3,
    })
    assert resp.status_code == 201
    assert "carlos@x.com" in server._load_users()


def test_admin_bipadores_rejeita_senha_errada(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={
        "adminPassword": "errada", "name": "Carlos", "email": "carlos@x.com", "filialId": 3,
    })
    assert resp.status_code == 403
    assert "carlos@x.com" not in server._load_users()


def test_admin_bipadores_exige_campos_obrigatorios(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    resp = client.post("/api/admin/bipadores", json={"adminPassword": "segredo123", "name": "Carlos"})
    assert resp.status_code == 400


def test_admin_bipadores_rejeita_email_duplicado(client, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "segredo123")
    payload = {"adminPassword": "segredo123", "name": "Carlos", "email": "carlos@x.com", "filialId": 3}
    client.post("/api/admin/bipadores", json=payload)
    resp = client.post("/api/admin/bipadores", json=payload)
    assert resp.status_code == 409


def test_auth_register_nao_existe_mais(client):
    resp = client.post("/api/auth/register", json={"name": "X", "email": "x@x.com", "filialId": 1})
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_local_persistence.py -v`
Expected: os testes novos (`test_audit_session_start_reusa_sessao_da_mesma_loja_no_mesmo_dia`, `test_audit_session_start_cria_sessao_separada_para_loja_diferente`, `test_audit_session_finish_nao_existe_mais`, `test_admin_login_*`, `test_admin_bipadores_*`, `test_auth_register_nao_existe_mais`) falham — a maioria com 404 (rota não existe) ou `AttributeError`/`AssertionError` porque `audit_session_start` ainda sempre cria uma sessão nova.

- [ ] **Step 3: Implementar**

Em `server.py`, no topo do arquivo, adicione o import `hmac`:

Find:
```python
import json
import os
import time
import threading
from datetime import date, datetime
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
```

Replace:
```python
import hmac
import json
import os
import time
import threading
from datetime import date, datetime
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
```

Adicione a variável `ADMIN_PASSWORD`:

Find:
```python
API_BASE = "https://api.i9logic.net/v1"
CLIENT_ID = os.environ["I9LOGIC_CLIENT_ID"]
TOKEN = os.environ["I9LOGIC_TOKEN"]
DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
```

Replace:
```python
API_BASE = "https://api.i9logic.net/v1"
CLIENT_ID = os.environ["I9LOGIC_CLIENT_ID"]
TOKEN = os.environ["I9LOGIC_TOKEN"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
```

Modifique `audit_session_start` para reaproveitar a sessão do dia daquela loja:

Find:
```python
@app.route("/api/audit/session/start", methods=["POST"])
def audit_session_start():
    data = request.get_json(silent=True) or {}
    filial_id = data.get("filialId")
    filial_nome = data.get("filialNome") or ""
    user_email = (data.get("userEmail") or "").strip().lower()
    user_name = (data.get("userName") or "").strip()
    if filial_id is None or not user_email:
        return jsonify({"ok": False, "error": "filialId e userEmail sao obrigatorios."}), 400
    if user_email not in _load_users():
        return jsonify({"ok": False, "error": "Usuario nao cadastrado."}), 403
    with _audit_lock:
        sessions = _load_audit()
        hoje = date.today().isoformat()
        session_id = f"audit_{filial_id}_{hoje}_{int(time.time() * 1000)}"
        session = {
            "id": session_id, "userEmail": user_email, "userName": user_name,
            "filialId": filial_id, "filialNome": filial_nome, "data": hoje,
            "inicio": datetime.now().strftime("%H:%M:%S"), "fim": "",
            "encontrados": {},
        }
        sessions[session_id] = session
        _save_audit(sessions)
    return jsonify({"ok": True, "session": session}), 201
```

Replace:
```python
@app.route("/api/audit/session/start", methods=["POST"])
def audit_session_start():
    data = request.get_json(silent=True) or {}
    filial_id = data.get("filialId")
    filial_nome = data.get("filialNome") or ""
    user_email = (data.get("userEmail") or "").strip().lower()
    user_name = (data.get("userName") or "").strip()
    if filial_id is None or not user_email:
        return jsonify({"ok": False, "error": "filialId e userEmail sao obrigatorios."}), 400
    if user_email not in _load_users():
        return jsonify({"ok": False, "error": "Usuario nao cadastrado."}), 403
    with _audit_lock:
        sessions = _load_audit()
        hoje = date.today().isoformat()
        # Sessao compartilhada por loja+dia: bipadores da mesma loja no mesmo dia
        # caem na mesma sessao (nao criam uma nova a cada login), para verem uns
        # aos outros o que ja foi confirmado/contado.
        existing = next((s for s in sessions.values()
                          if s["filialId"] == filial_id and s["data"] == hoje), None)
        if existing:
            return jsonify({"ok": True, "session": existing}), 200
        session_id = f"audit_{filial_id}_{hoje}_{int(time.time() * 1000)}"
        session = {
            "id": session_id, "userEmail": user_email, "userName": user_name,
            "filialId": filial_id, "filialNome": filial_nome, "data": hoje,
            "inicio": datetime.now().strftime("%H:%M:%S"),
            "encontrados": {},
        }
        sessions[session_id] = session
        _save_audit(sessions)
    return jsonify({"ok": True, "session": session}), 201
```

Remova a rota `audit_session_finish` inteira (sem uso depois que a sessao passa a ser compartilhada — a virada de dia ja garante sessao nova via a checagem de data acima):

Find:
```python
@app.route("/api/audit/session/finish", methods=["POST"])
def audit_session_finish():
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId")
    if not session_id:
        return jsonify({"ok": False, "error": "sessionId e obrigatorio."}), 400
    with _audit_lock:
        sessions = _load_audit()
        session = sessions.get(session_id)
        if not session:
            return jsonify({"ok": False, "error": "Sessao nao encontrada."}), 404
        session["fim"] = datetime.now().strftime("%H:%M:%S")
        _save_audit(sessions)
    return jsonify({"ok": True, "session": session})


```

Replace: (nada — remova o bloco inteiro, incluindo a linha em branco extra logo depois)

Substitua a rota de autocadastro aberto pelas duas rotas de admin:

Find:
```python
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    filial_id = data.get("filialId")
    if not name or not email or filial_id is None:
        return jsonify({"ok": False, "error": "Nome, email e loja são obrigatórios."}), 400
    users = _load_users()
    if email in users:
        return jsonify({"ok": False, "error": "Email já cadastrado."}), 409
    users[email] = {"name": name, "email": email, "filialId": filial_id}
    _save_users(users)
    return jsonify({"ok": True, "user": users[email]}), 201
```

Replace:
```python
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return jsonify({"ok": False, "error": "Senha incorreta."}), 401
    return jsonify({"ok": True})


@app.route("/api/admin/bipadores", methods=["POST"])
def admin_create_bipador():
    data = request.get_json(silent=True) or {}
    admin_password = data.get("adminPassword") or ""
    if not hmac.compare_digest(admin_password, ADMIN_PASSWORD):
        return jsonify({"ok": False, "error": "Senha de administrador incorreta."}), 403
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    filial_id = data.get("filialId")
    if not name or not email or filial_id is None:
        return jsonify({"ok": False, "error": "Nome, email e loja são obrigatórios."}), 400
    users = _load_users()
    if email in users:
        return jsonify({"ok": False, "error": "Email já cadastrado."}), 409
    users[email] = {"name": name, "email": email, "filialId": filial_id}
    _save_users(users)
    return jsonify({"ok": True, "user": users[email]}), 201
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `python -m pytest tests/test_local_persistence.py -v`
Expected: PASS — todos os testes do arquivo, incluindo os novos e os já existentes (sem regressão).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_local_persistence.py
git commit -m "feat: admin unico, cadastro de bipador restrito, sessao por loja+dia"
```

---

### Task 2: Frontend — fluxo unificado de bipagem + aba Bipadores (admin)

**Files:**
- Modify: `templates/index.html`

**Interfaces:**
- Consumes: `POST /api/admin/login`, `POST /api/admin/bipadores`, `GET /api/auth/users` (já existe), comportamento novo de `POST /api/audit/session/start` — todos da Task 1. Funções já existentes que continuam sem mudança: `applyCountDelta`, `setCountForProduct`, `syncCountToServer`, `renderCountList`, `updateAuditUI`, `findByEan`, `resolveProduct`, `renderSearchResults`, `showProductDetails`, `playBeep`/`playBuzzer`, `showScanFeedback`.
- Produces: `registerHit(product)`, `switchAuditPanel(panel)`, `updateNavForRole()`, `handleAdminLogin()`, `handleCreateBipador()`, `loadBipadoresList()`, `popAdminFilialSelect()` — nenhuma outra tarefa deste plano depende deles (última tarefa de código).

- [ ] **Step 1: Remover CSS não usado (`.auth-tab`)**

Find:
```css
.auth-tab{padding:10px;border:1px solid var(--border);background:var(--bg);color:var(--muted);font-weight:600;cursor:pointer;text-align:center;font-size:.85rem}
.auth-tab.active{background:var(--p600);color:#fff;border-color:var(--p600)}
```

Replace: (remova as duas linhas)

- [ ] **Step 2: Reestruturar o modal de login (remove autocadastro, adiciona toggle de admin)**

Find:
```html
<!-- SETUP MODAL -->
<div id="setup-modal">
  <div class="card">
    <h2>Auditoria de Estoque</h2>
    <div style="display:flex;gap:0;margin-bottom:16px">
      <button class="auth-tab active" data-tab="login" style="flex:1;border-radius:8px 0 0 8px">Entrar</button>
      <button class="auth-tab" data-tab="register" style="flex:1;border-radius:0 8px 8px 0">Cadastrar</button>
    </div>
    <div id="auth-login" style="display:flex;flex-direction:column;gap:12px">
      <input id="login-email" type="email" placeholder="Email" autocomplete="email">
      <input id="login-name" placeholder="Nome completo" autocomplete="name">
      <button class="btn-p" id="btn-login">Entrar</button>
      <div id="login-error" style="color:var(--d600);font-size:.85rem;display:none"></div>
    </div>
    <div id="auth-register" style="display:none;flex-direction:column;gap:12px">
      <input id="reg-name" placeholder="Nome completo" autocomplete="name">
      <input id="reg-email" type="email" placeholder="Email" autocomplete="email">
      <select id="reg-filial"><option value="">Carregando lojas...</option></select>
      <button class="btn-s" id="btn-register">Cadastrar</button>
      <div id="reg-error" style="color:var(--d600);font-size:.85rem;display:none"></div>
    </div>
    <div style="font-size:.75rem;color:var(--muted);margin-top:8px">Dados carregados: <span id="cache-status">0 produtos</span></div>
  </div>
</div>
```

Replace:
```html
<!-- SETUP MODAL -->
<div id="setup-modal">
  <div class="card">
    <h2>Auditoria de Estoque</h2>
    <div id="auth-login" style="display:flex;flex-direction:column;gap:12px">
      <input id="login-email" type="email" placeholder="Email" autocomplete="email">
      <input id="login-name" placeholder="Nome completo" autocomplete="name">
      <button class="btn-p" id="btn-login">Entrar</button>
      <div id="login-error" style="color:var(--d600);font-size:.85rem;display:none"></div>
      <div style="text-align:center;margin-top:4px">
        <a href="#" id="link-admin-toggle" style="font-size:.8rem;color:var(--muted)">Sou administrador</a>
      </div>
    </div>
    <div id="auth-admin" style="display:none;flex-direction:column;gap:12px">
      <input id="admin-password" type="password" placeholder="Senha de administrador">
      <button class="btn-p" id="btn-admin-login">Entrar como Admin</button>
      <div id="admin-error" style="color:var(--d600);font-size:.85rem;display:none"></div>
      <div style="text-align:center;margin-top:4px">
        <a href="#" id="link-bipador-toggle" style="font-size:.8rem;color:var(--muted)">Sou bipador</a>
      </div>
    </div>
    <div style="font-size:.75rem;color:var(--muted);margin-top:8px">Dados carregados: <span id="cache-status">0 produtos</span></div>
  </div>
</div>
```

- [ ] **Step 3: Adicionar o item de navegação "Bipadores" (oculto por padrão)**

Find:
```html
    <nav>
      <button data-view="dashboard" class="active">Dashboard</button>
      <button data-view="audit">Auditoria</button>
      <button data-view="reports">Relatórios</button>
      <button data-view="debug">Diagnóstico API</button>
      <button class="btn-s btn-sm" id="btn-reload-data">Recarregar Dados</button>
    </nav>
```

Replace:
```html
    <nav>
      <button data-view="dashboard" class="active">Dashboard</button>
      <button data-view="audit">Auditoria</button>
      <button data-view="reports">Relatórios</button>
      <button data-view="debug">Diagnóstico API</button>
      <button data-view="bipadores" style="display:none">Bipadores</button>
      <button class="btn-s btn-sm" id="btn-reload-data">Recarregar Dados</button>
    </nav>
```

- [ ] **Step 4: Reestruturar a view Auditoria em 3 sub-abas**

Find:
```html
    <!-- AUDIT -->
    <div id="audit-view" style="display:none">
      <div class="report-tabs">
        <button class="report-tab active" id="btn-mode-verificacao">Verificação</button>
        <button class="report-tab" id="btn-mode-contagem">Contagem</button>
      </div>
      <div class="audit-layout">
        <div class="scan-zone">
          <input id="scan-input" class="scan-input" placeholder="Bipe o código de barras aqui..." autocomplete="off" inputmode="numeric">
          <div class="scan-actions">
            <button class="btn-p" id="btn-camera">Câmera</button>
            <input id="manual-search" placeholder="Buscar por nome, SKU, EAN ou NCM..." style="flex:1;min-width:150px">
            <button class="btn-o" id="btn-print-label">Imprimir Etiqueta</button>
          </div>
          <div id="scan-feedback"></div>
          <div id="search-results" style="display:none"></div>
          <div id="verificacao-panel">
            <div id="scan-stats" style="display:flex;gap:16px;justify-content:center;padding:8px;font-size:.85rem;font-weight:600">
              <span style="color:var(--s600)">🆕 Novos: <strong id="stat-new">0</strong></span>
              <span style="color:var(--w600)">🔄 Duplicados: <strong id="stat-dup">0</strong></span>
              <span style="color:var(--p600)">📦 Total: <strong id="stat-total">0</strong></span>
            </div>
            <h4 style="margin-top:8px">Últimos bipados (<span id="found-count">0</span> encontrados)</h4>
            <div class="recent-scans" id="recent-scans-list"></div>
          </div>
          <div id="contagem-panel" style="display:none">
            <h4 style="margin-top:8px">Contagem desta sessão (<span id="count-total">0</span> produtos)</h4>
            <div class="recent-scans" id="count-list"></div>
          </div>
        </div>
        <div class="product-panel" id="product-panel">
          <div class="header">Detalhes do Produto</div>
          <div class="body" id="product-panel-body">
            <span style="color:var(--muted);font-size:.85rem">Bipe um produto para ver os detalhes.</span>
          </div>
        </div>
      </div>
    </div>
```

Replace:
```html
    <!-- AUDIT -->
    <div id="audit-view" style="display:none">
      <div class="report-tabs">
        <button class="report-tab active" id="btn-mode-bipagem">Bipagem</button>
        <button class="report-tab" id="btn-mode-existentes">Produtos Existentes</button>
        <button class="report-tab" id="btn-mode-contado">Estoque Contado</button>
      </div>
      <div class="audit-layout">
        <div class="scan-zone">
          <div id="bipagem-panel">
            <input id="scan-input" class="scan-input" placeholder="Bipe o código de barras aqui..." autocomplete="off" inputmode="numeric">
            <div class="scan-actions">
              <button class="btn-p" id="btn-camera">Câmera</button>
              <input id="manual-search" placeholder="Buscar por nome, SKU, EAN ou NCM..." style="flex:1;min-width:150px">
              <button class="btn-o" id="btn-print-label">Imprimir Etiqueta</button>
            </div>
            <div id="scan-feedback"></div>
            <div id="search-results" style="display:none"></div>
            <div id="scan-stats" style="display:flex;gap:16px;justify-content:center;padding:8px;font-size:.85rem;font-weight:600">
              <span style="color:var(--s600)">📋 Produtos Existentes: <strong id="stat-existentes">0</strong></span>
              <span style="color:var(--p600)">📦 Itens Contados: <strong id="stat-contados">0</strong></span>
            </div>
          </div>
          <div id="existentes-panel" style="display:none">
            <h4 style="margin-top:8px">Produtos Existentes (<span id="found-count">0</span>)</h4>
            <div class="recent-scans" id="recent-scans-list"></div>
          </div>
          <div id="contado-panel" style="display:none">
            <h4 style="margin-top:8px">Estoque Contado (<span id="count-total">0</span>)</h4>
            <div class="recent-scans" id="count-list"></div>
          </div>
        </div>
        <div class="product-panel" id="product-panel">
          <div class="header">Detalhes do Produto</div>
          <div class="body" id="product-panel-body">
            <span style="color:var(--muted);font-size:.85rem">Bipe um produto para ver os detalhes.</span>
          </div>
        </div>
      </div>
    </div>
```

- [ ] **Step 5: Adicionar a view "Bipadores" (admin)**

Find:
```html
    <!-- DEBUG -->
    <div id="debug-view" style="display:none">
      <div class="card" style="margin-bottom:16px">
        <h3 style="margin-bottom:12px">Conexão com a API (api.i9logic.net)</h3>
        <button class="btn-p btn-sm" id="btn-test-connection">Testar Conexão</button>
        <div id="debug-connection-result" style="margin-top:12px;display:flex;flex-direction:column;gap:6px"></div>
      </div>
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <h3>Produtos Sincronizados (compartilhado entre todos os usuários)</h3>
          <button class="btn-s btn-sm" id="btn-pull-200">Atualizar Amostra</button>
        </div>
        <div id="debug-pull-status" style="font-size:.85rem;color:var(--muted);margin-bottom:8px"></div>
        <div style="overflow-x:auto;max-height:70vh"><table class="report-table" id="debug-table"><thead></thead><tbody></tbody></table></div>
      </div>
    </div>
  </main>
</div>
```

Replace:
```html
    <!-- DEBUG -->
    <div id="debug-view" style="display:none">
      <div class="card" style="margin-bottom:16px">
        <h3 style="margin-bottom:12px">Conexão com a API (api.i9logic.net)</h3>
        <button class="btn-p btn-sm" id="btn-test-connection">Testar Conexão</button>
        <div id="debug-connection-result" style="margin-top:12px;display:flex;flex-direction:column;gap:6px"></div>
      </div>
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <h3>Produtos Sincronizados (compartilhado entre todos os usuários)</h3>
          <button class="btn-s btn-sm" id="btn-pull-200">Atualizar Amostra</button>
        </div>
        <div id="debug-pull-status" style="font-size:.85rem;color:var(--muted);margin-bottom:8px"></div>
        <div style="overflow-x:auto;max-height:70vh"><table class="report-table" id="debug-table"><thead></thead><tbody></tbody></table></div>
      </div>
    </div>
    <!-- BIPADORES (admin) -->
    <div id="bipadores-view" style="display:none">
      <div class="card" style="margin-bottom:16px">
        <h3 style="margin-bottom:12px">Novo Bipador</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <input id="bip-name" placeholder="Nome completo" style="flex:1;min-width:150px">
          <input id="bip-email" type="email" placeholder="Email" style="flex:1;min-width:150px">
          <select id="bip-filial" style="flex:1;min-width:150px"><option value="">Carregando lojas...</option></select>
          <button class="btn-s" id="btn-create-bipador">Cadastrar</button>
        </div>
        <div id="bip-error" style="color:var(--d600);font-size:.85rem;display:none;margin-top:8px"></div>
      </div>
      <div id="bipadores-list"></div>
    </div>
  </main>
</div>
```

- [ ] **Step 6: Estado `S` — trocar `auditMode` por `auditPanel`, adicionar campos de admin**

Find:
```js
let S = {
  user: null, // { name, email, filialId } - logged-in user
  view: 'loading',
  auditMode: 'verificacao', // 'verificacao' | 'contagem'
  dataReady: false,
  products: [],
  productsMap: {},
  productsByEan: {},
  productsByCodigo: {},
  filiais: [],
  stockMap: {},
  pricesMap: {},
  session: null,
  allSessions: [],
  cameraStream: null,
  cameraActive: false,
  barcodeDetector: null,
};
```

Replace:
```js
let S = {
  user: null, // { name, email, filialId } - logged-in user
  view: 'loading',
  auditPanel: 'bipagem', // 'bipagem' | 'existentes' | 'contado'
  isAdmin: false,
  adminPassword: null,
  dataReady: false,
  products: [],
  productsMap: {},
  productsByEan: {},
  productsByCodigo: {},
  filiais: [],
  stockMap: {},
  pricesMap: {},
  session: null,
  allSessions: [],
  cameraStream: null,
  cameraActive: false,
  barcodeDetector: null,
};
```

- [ ] **Step 7: Simplificar `markFound` (não precisa mais sinalizar duplicado — quem chama já checou antes)**

Find:
```js
function markFound(productId, ean, descricao) {
  if (!S.session) return false;
  if (S.session.encontrados[productId]) {
    S.session._dupCount = (S.session._dupCount || 0) + 1;
    return 'dup';
  }
  S.session.encontrados[productId] = {
    ean, descricao,
    scannedAt: new Date().toLocaleTimeString('pt-BR'),
  };
  syncScanToServer(productId, ean, descricao);
  return true;
}
```

Replace:
```js
function markFound(productId, ean, descricao) {
  if (!S.session) return;
  S.session.encontrados[productId] = {
    ean, descricao,
    scannedAt: new Date().toLocaleTimeString('pt-BR'),
  };
  syncScanToServer(productId, ean, descricao);
}
```

- [ ] **Step 8: Remover `finalizeSession` (sessão compartilhada não deve mais ser encerrada a cada logout)**

Find:
```js
async function finalizeSession() {
  if (!S.session) return;
  S.session.fim = new Date().toLocaleTimeString('pt-BR');
  try {
    await fetch('/api/audit/session/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: S.session.id }),
    });
  } catch (e) { /* best-effort */ }
}

async function loadSessions() {
```

Replace:
```js
async function loadSessions() {
```

- [ ] **Step 9: Unificar `processScan` em `registerHit` + `updateScanStats`**

Find:
```js
/* ===== PROCESS SCAN ===== */
function processScan(raw) {
  if (!S.session) { toast('Inicie uma auditoria primeiro.', 'err'); return; }
  const barcode = fmt(raw);
  if (!barcode) return;

  const matches = findByEan(barcode);
  if (!matches || matches.length === 0) {
    showScanFeedback('❌ CÓDIGO NÃO ENCONTRADO NO CATÁLOGO', 'err');
    updateScanStats();
    return;
  }

  let product = matches[0];
  const isDup = markFound(product.id, barcode, fmt(product.descricao)) === 'dup';

  if (isDup) {
    showScanFeedback('⚠️ PRODUTO JÁ REGISTRADO — ' + product.descricao, 'dup');
    playBuzzer();
  } else {
    showScanFeedback('✅ NOVO PRODUTO REGISTRADO — ' + product.descricao, 'ok');
    playBeep();
  }
  showProductDetails(product);
  updateScanStats();
  updateAuditUI();
  updateDashboardUI();
  $('#scan-input').focus();
}

function updateScanStats() {
  if (!S.session) return;
  const all = Object.keys(S.session.encontrados).length;
  S.session._dupCount = S.session._dupCount || 0;
  const newCount = all - S.session._dupCount;
  $('#stat-new').textContent = newCount;
  $('#stat-dup').textContent = S.session._dupCount;
  $('#stat-total').textContent = all;
}
```

Replace:
```js
/* ===== PROCESS SCAN/CONTAGEM (fluxo unico) ===== */
// 1o bipe/selecao de um produto confirma existencia (Produtos Existentes).
// 2o bipe/selecao em diante do MESMO produto soma +1 na contagem (Estoque Contado).
// Sem alternancia manual de modo — a repeticao decide.
function registerHit(product) {
  if (!S.session) { toast('Inicie uma auditoria primeiro.', 'err'); return; }
  const existing = S.session.encontrados[product.id];
  if (!existing) {
    markFound(product.id, fmt(product.ean) || 'manual', fmt(product.descricao));
    showScanFeedback('✅ PRODUTO EXISTENTE — ' + product.descricao, 'ok');
    playBeep();
  } else {
    applyCountDelta(product.id, 1);
    const qtd = S.session.encontrados[product.id].qtd;
    showScanFeedback(`📦 CONTANDO — ${product.descricao}: ${qtd} un.`, 'ok');
    playBeep();
  }
  showProductDetails(product);
  updateScanStats();
  updateAuditUI();
  updateDashboardUI();
}

function updateScanStats() {
  if (!S.session) return;
  const existentes = Object.keys(S.session.encontrados).length;
  const contados = Object.values(S.session.encontrados).filter(r => r.qtd != null).length;
  $('#stat-existentes').textContent = existentes;
  $('#stat-contados').textContent = contados;
}
```

- [ ] **Step 10: Simplificar `dispatchScan` (sem mais alternância de modo — bipagem sempre resolve por EAN e cai no fluxo único)**

Find:
```js
/* ===== SCANNER INPUT ===== */
// Dispara o scan pelo sub-modo ativo (Verificação/Contagem) — fonte unica usada
// pelo leitor USB/teclado, pela câmera e pela leitura de imagem, para que os
// três nunca divirjam sobre qual fluxo um código bipado deve seguir.
function dispatchScan(barcode) {
  if (S.auditMode === 'contagem') processCountScan(barcode);
  else processScan(barcode);
}

function initScannerInput() {
  const inp = $('#scan-input');
  if (!inp) return;
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const val = inp.value.trim();
      inp.value = '';
      if (!val) return;
      dispatchScan(val);
    }
  });
}
```

Replace:
```js
/* ===== SCANNER INPUT ===== */
// Bipagem (leitor USB/teclado, câmera, leitura de imagem) sempre resolve por EAN
// exato — fonte unica usada pelos tres, para que nunca divirjam sobre como um
// codigo bipado fisicamente deve ser resolvido.
function dispatchScan(barcode) {
  const matches = findByEan(barcode);
  if (!matches || matches.length === 0) {
    showScanFeedback('❌ CÓDIGO NÃO ENCONTRADO NO CATÁLOGO', 'err');
    return;
  }
  registerHit(matches[0]);
  $('#scan-input').focus();
}

function initScannerInput() {
  const inp = $('#scan-input');
  if (!inp) return;
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const val = inp.value.trim();
      inp.value = '';
      if (!val) return;
      dispatchScan(val);
    }
  });
}
```

- [ ] **Step 11: Simplificar `selectManualProduct` e remover `processCountScan`/`switchAuditMode`**

Find:
```js
function selectManualProduct(productId) {
  const p = S.productsMap[productId];
  if (!p) return;
  if (S.auditMode === 'contagem') {
    if (!S.session) { toast('Inicie uma auditoria primeiro.', 'err'); return; }
    applyCountDelta(productId, 1);
    const qtd = S.session.encontrados[productId].qtd;
    showScanFeedback(`✅ ${p.descricao} — contagem: ${qtd}`, 'ok');
    playBeep();
  } else {
    const result = markFound(productId, fmt(p.ean) || 'manual', fmt(p.descricao));
    if (result === 'dup') {
      showScanFeedback('⚠️ PRODUTO JÁ REGISTRADO — ' + p.descricao, 'dup');
      playBuzzer();
    } else {
      showScanFeedback('✅ NOVO PRODUTO REGISTRADO (manual) — ' + p.descricao, 'ok');
      playBeep();
    }
    updateScanStats();
    updateAuditUI();
  }
  showProductDetails(p);
  updateDashboardUI();
  $('#search-results').style.display = 'none';
  $('#manual-search').value = '';
}
```

Replace:
```js
function selectManualProduct(productId) {
  const p = S.productsMap[productId];
  if (!p) return;
  registerHit(p);
  $('#search-results').style.display = 'none';
  $('#manual-search').value = '';
}
```

Depois, mais abaixo no arquivo (dentro da seção `/* ===== CONTAGEM ===== */`), remova `processCountScan` e substitua `switchAuditMode` por `switchAuditPanel`:

Find:
```js
function processCountScan(raw) {
  if (!S.session) { toast('Inicie uma auditoria primeiro.', 'err'); return; }
  const barcode = fmt(raw);
  if (!barcode) return;
  const matches = findByEan(barcode);
  if (!matches || matches.length === 0) {
    showScanFeedback('❌ CÓDIGO NÃO ENCONTRADO NO CATÁLOGO', 'err');
    return;
  }
  const product = matches[0];
  applyCountDelta(product.id, 1);
  const qtd = S.session.encontrados[product.id].qtd;
  showScanFeedback(`✅ ${product.descricao} — contagem: ${qtd}`, 'ok');
  playBeep();
  showProductDetails(product);
  updateDashboardUI();
  $('#scan-input').focus();
}

function switchAuditMode(mode) {
  S.auditMode = mode;
  $('#btn-mode-verificacao').classList.toggle('active', mode === 'verificacao');
  $('#btn-mode-contagem').classList.toggle('active', mode === 'contagem');
  $('#verificacao-panel').style.display = mode === 'verificacao' ? 'block' : 'none';
  $('#contagem-panel').style.display = mode === 'contagem' ? 'block' : 'none';
  $('#search-results').style.display = 'none';
  if (mode === 'contagem') renderCountList();
  else updateAuditUI();
}
```

Replace:
```js
function switchAuditPanel(panel) {
  S.auditPanel = panel;
  $('#btn-mode-bipagem').classList.toggle('active', panel === 'bipagem');
  $('#btn-mode-existentes').classList.toggle('active', panel === 'existentes');
  $('#btn-mode-contado').classList.toggle('active', panel === 'contado');
  $('#bipagem-panel').style.display = panel === 'bipagem' ? 'block' : 'none';
  $('#existentes-panel').style.display = panel === 'existentes' ? 'block' : 'none';
  $('#contado-panel').style.display = panel === 'contado' ? 'block' : 'none';
  $('#search-results').style.display = 'none';
  if (panel === 'existentes') updateAuditUI();
  else if (panel === 'contado') renderCountList();
}
```

- [ ] **Step 12: `switchView` — entrar em Auditoria sempre pela aba Bipagem, adicionar caso `bipadores`**

Find:
```js
async function switchView(view) {
  S.view = view;
  ['loading-view', 'dashboard-view', 'audit-view', 'reports-view', 'debug-view'].forEach(id => $(`#${id}`).style.display = 'none');
  if (view === 'dashboard') {
    $('#dashboard-view').style.display = 'block';
    renderDashboard();
    await loadSessions(); // busca progresso atualizado de todos os usuários no servidor
    if (S.view === 'dashboard') renderDashboard();
  }
  else if (view === 'audit') { $('#audit-view').style.display = 'block'; updateAuditUI(); setTimeout(() => $('#scan-input').focus(), 200); }
  else if (view === 'reports') { $('#reports-view').style.display = 'block'; renderReports(); }
  else if (view === 'debug') { $('#debug-view').style.display = 'block'; pullSyncedProducts(); }
  else if (view === 'loading') { $('#loading-view').style.display = 'flex'; }
  $$('nav button[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
}
```

Replace:
```js
async function switchView(view) {
  S.view = view;
  ['loading-view', 'dashboard-view', 'audit-view', 'reports-view', 'debug-view', 'bipadores-view'].forEach(id => $(`#${id}`).style.display = 'none');
  if (view === 'dashboard') {
    $('#dashboard-view').style.display = 'block';
    renderDashboard();
    await loadSessions(); // busca progresso atualizado de todos os usuários no servidor
    if (S.view === 'dashboard') renderDashboard();
  }
  else if (view === 'audit') { $('#audit-view').style.display = 'block'; switchAuditPanel('bipagem'); setTimeout(() => $('#scan-input').focus(), 200); }
  else if (view === 'reports') { $('#reports-view').style.display = 'block'; renderReports(); }
  else if (view === 'debug') { $('#debug-view').style.display = 'block'; pullSyncedProducts(); }
  else if (view === 'bipadores') { $('#bipadores-view').style.display = 'block'; popAdminFilialSelect(); loadBipadoresList(); }
  else if (view === 'loading') { $('#loading-view').style.display = 'flex'; }
  $$('nav button[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
}
```

- [ ] **Step 13: Dashboard — separar "Produtos Existentes" de "Estoque Contado"**

Find:
```js
function getAuditStats() {
  const total = S.products.length;
  const found = S.session ? Object.keys(S.session.encontrados).length : 0;
  const pending = total - found;
  const pct = total > 0 ? Math.round((found / total) * 100) : 0;
  return { total, found, pending, pct };
}
```

Replace:
```js
function getAuditStats() {
  const total = S.products.length;
  const found = S.session ? Object.keys(S.session.encontrados).length : 0;
  const contados = S.session ? Object.values(S.session.encontrados).filter(r => r.qtd != null).length : 0;
  const pending = total - found;
  const pct = total > 0 ? Math.round((found / total) * 100) : 0;
  return { total, found, contados, pending, pct };
}
```

Find:
```js
  $('#dash-stats').innerHTML = `
    <div class="card stat-card"><div class="num" style="color:var(--p600)">${fmtNum(st.total)}</div><div class="lbl">Cadastrados</div></div>
    <div class="card stat-card"><div class="num" style="color:var(--s600)">${fmtNum(st.found)}</div><div class="lbl">Encontrados</div></div>
    <div class="card stat-card"><div class="num" style="color:var(--d600)">${fmtNum(st.pending)}</div><div class="lbl">Pendentes</div></div>
    <div class="card stat-card"><div class="num" style="color:var(--p500)">${st.pct}%</div><div class="lbl">Auditado</div></div>
  `;
```

Replace:
```js
  $('#dash-stats').innerHTML = `
    <div class="card stat-card"><div class="num" style="color:var(--p600)">${fmtNum(st.total)}</div><div class="lbl">Cadastrados</div></div>
    <div class="card stat-card"><div class="num" style="color:var(--s600)">${fmtNum(st.found)}</div><div class="lbl">Produtos Existentes</div></div>
    <div class="card stat-card"><div class="num" style="color:var(--p600)">${fmtNum(st.contados)}</div><div class="lbl">Estoque Contado</div></div>
    <div class="card stat-card"><div class="num" style="color:var(--d600)">${fmtNum(st.pending)}</div><div class="lbl">Pendentes</div></div>
    <div class="card stat-card"><div class="num" style="color:var(--p500)">${st.pct}%</div><div class="lbl">Auditado</div></div>
  `;
```

- [ ] **Step 14: Seção AUTH — remover autocadastro, adicionar login/gestão de admin**

Find:
```js
/* ===== AUTH ===== */
function popRegFilial() {
  const sel = $('#reg-filial');
  if (!sel) return;
  sel.innerHTML = '<option value="">Selecione sua loja</option>' +
    S.filiais.map(f => `<option value="${f.id}">${f.fantasia || f.razaosocial} (${fmt(f.codigo)})</option>`).join('');
}

function showAuthError(tab, msg) {
  const el = $('#' + tab + '-error');
  el.textContent = msg;
  el.style.display = 'block';
}

function clearAuthErrors() {
  ['login', 'reg'].forEach(t => { const e = $('#' + t + '-error'); if (e) e.style.display = 'none'; });
}

async function handleLogin() {
  clearAuthErrors();
  const email = $('#login-email').value.trim();
  const name = $('#login-name').value.trim();
  if (!email || !name) { showAuthError('login', 'Preencha email e nome.'); return; }
  const result = await apiAuth('login', { email, name });
  if (!result.ok) { showAuthError('login', result.error); return; }
  onAuthSuccess(result.user);
}

async function handleRegister() {
  clearAuthErrors();
  const name = $('#reg-name').value.trim();
  const email = $('#reg-email').value.trim();
  const filialId = Number($('#reg-filial').value);
  if (!name || !email || !filialId) { showAuthError('reg', 'Preencha todos os campos.'); return; }
  const result = await apiAuth('register', { name, email, filialId });
  if (!result.ok) { showAuthError('reg', result.error); return; }
  onAuthSuccess(result.user);
}
```

Replace:
```js
/* ===== AUTH ===== */
function showAuthError(tab, msg) {
  const el = $('#' + tab + '-error');
  el.textContent = msg;
  el.style.display = 'block';
}

function clearAuthErrors() {
  ['login', 'admin', 'bip'].forEach(t => { const e = $('#' + t + '-error'); if (e) e.style.display = 'none'; });
}

async function handleLogin() {
  clearAuthErrors();
  const email = $('#login-email').value.trim();
  const name = $('#login-name').value.trim();
  if (!email || !name) { showAuthError('login', 'Preencha email e nome.'); return; }
  const result = await apiAuth('login', { email, name });
  if (!result.ok) { showAuthError('login', result.error); return; }
  onAuthSuccess(result.user);
}

/* ===== ADMIN ===== */
async function handleAdminLogin() {
  clearAuthErrors();
  const password = $('#admin-password').value;
  if (!password) { showAuthError('admin', 'Digite a senha.'); return; }
  const resp = await fetch('/api/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  const r = await resp.json();
  if (!r.ok) { showAuthError('admin', r.error || 'Senha incorreta.'); return; }
  S.isAdmin = true;
  S.adminPassword = password;
  $('#setup-modal').style.display = 'none';
  $('#app').classList.add('active');
  $('#hdr-filial').textContent = 'Admin';
  $('#hdr-auditor').textContent = 'Administrador';
  updateNavForRole();
  switchView('bipadores');
}

function updateNavForRole() {
  const adminOnly = ['bipadores'];
  const bipadorOnly = ['dashboard', 'audit', 'reports', 'debug'];
  $$('nav button[data-view]').forEach(b => {
    const v = b.dataset.view;
    if (adminOnly.includes(v)) b.style.display = S.isAdmin ? '' : 'none';
    else if (bipadorOnly.includes(v)) b.style.display = S.isAdmin ? 'none' : '';
  });
  $('#btn-reload-data').style.display = S.isAdmin ? 'none' : '';
}

function popAdminFilialSelect() {
  const sel = $('#bip-filial');
  if (!sel) return;
  sel.innerHTML = '<option value="">Selecione a loja</option>' +
    S.filiais.map(f => `<option value="${f.id}">${f.fantasia || f.razaosocial} (${fmt(f.codigo)})</option>`).join('');
}

async function loadBipadoresList() {
  const r = await apiGet('auth/users');
  const users = r.users || [];
  const byFilial = {};
  for (const u of users) {
    if (!byFilial[u.filialId]) byFilial[u.filialId] = [];
    byFilial[u.filialId].push(u);
  }
  $('#bipadores-list').innerHTML = S.filiais.map(f => {
    const list = byFilial[f.id] || [];
    return `<div class="card" style="margin-bottom:12px">
      <h4 style="margin-bottom:8px">${f.fantasia || f.razaosocial} (${list.length} bipador${list.length !== 1 ? 'es' : ''})</h4>
      ${list.length ? list.map(u => `
        <div class="recent-row" style="cursor:default">
          <span>${u.name}</span>
          <span style="color:var(--muted);font-size:.75rem">${u.email}</span>
        </div>
      `).join('') : '<div class="empty" style="padding:8px">Nenhum bipador cadastrado.</div>'}
    </div>`;
  }).join('');
}

async function handleCreateBipador() {
  clearAuthErrors();
  const name = $('#bip-name').value.trim();
  const email = $('#bip-email').value.trim();
  const filialId = Number($('#bip-filial').value);
  if (!name || !email || !filialId) { showAuthError('bip', 'Preencha todos os campos.'); return; }
  const resp = await fetch('/api/admin/bipadores', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ adminPassword: S.adminPassword, name, email, filialId }),
  });
  const r = await resp.json();
  if (!r.ok) { showAuthError('bip', r.error); return; }
  toast('Bipador cadastrado!', 'ok');
  $('#bip-name').value = '';
  $('#bip-email').value = '';
  $('#bip-filial').value = '';
  loadBipadoresList();
}
```

- [ ] **Step 15: `handleLogout` — não finalizar mais a sessão compartilhada, resetar estado de admin**

Find:
```js
function handleLogout() {
  if (S.session) finalizeSession();
  S.user = null;
  S.session = null;
  $('#app').classList.remove('active');
  $('#setup-modal').style.display = 'flex';
  $('#hdr-filial').textContent = '--';
  $('#hdr-auditor').textContent = '--';
  lsSet(LS_KEYS.state, null);
  if (S.cameraStream) stopCamera();
}
```

Replace:
```js
function handleLogout() {
  S.user = null;
  S.session = null;
  S.isAdmin = false;
  S.adminPassword = null;
  updateNavForRole();
  $('#app').classList.remove('active');
  $('#setup-modal').style.display = 'flex';
  $('#hdr-filial').textContent = '--';
  $('#hdr-auditor').textContent = '--';
  lsSet(LS_KEYS.state, null);
  if (S.cameraStream) stopCamera();
}
```

- [ ] **Step 16: `init()` — remover wiring do autocadastro, adicionar wiring de admin**

Find:
```js
  popRegFilial();

  // Event: auth tabs
  $$('.auth-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.auth-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      $('#auth-login').style.display = btn.dataset.tab === 'login' ? 'flex' : 'none';
      $('#auth-register').style.display = btn.dataset.tab === 'register' ? 'flex' : 'none';
      clearAuthErrors();
    });
  });

  // Event: login
  $('#btn-login').addEventListener('click', handleLogin);
  $('#login-name').addEventListener('keydown', e => { if (e.key === 'Enter') handleLogin(); });

  // Event: register
  $('#btn-register').addEventListener('click', handleRegister);
```

Replace:
```js
  updateNavForRole();

  // Event: login / admin toggle
  $('#link-admin-toggle').addEventListener('click', (e) => {
    e.preventDefault();
    $('#auth-login').style.display = 'none';
    $('#auth-admin').style.display = 'flex';
    clearAuthErrors();
  });
  $('#link-bipador-toggle').addEventListener('click', (e) => {
    e.preventDefault();
    $('#auth-admin').style.display = 'none';
    $('#auth-login').style.display = 'flex';
    clearAuthErrors();
  });

  // Event: login
  $('#btn-login').addEventListener('click', handleLogin);
  $('#login-name').addEventListener('keydown', e => { if (e.key === 'Enter') handleLogin(); });

  // Event: admin login
  $('#btn-admin-login').addEventListener('click', handleAdminLogin);
  $('#admin-password').addEventListener('keydown', e => { if (e.key === 'Enter') handleAdminLogin(); });

  // Event: create bipador
  $('#btn-create-bipador').addEventListener('click', handleCreateBipador);
```

Mais abaixo, no mesmo `init()`, atualize o wiring das abas da Auditoria:

Find:
```js
  // Event: audit mode tabs
  $('#btn-mode-verificacao').addEventListener('click', () => switchAuditMode('verificacao'));
  $('#btn-mode-contagem').addEventListener('click', () => switchAuditMode('contagem'));
```

Replace:
```js
  // Event: audit panel tabs
  $('#btn-mode-bipagem').addEventListener('click', () => switchAuditPanel('bipagem'));
  $('#btn-mode-existentes').addEventListener('click', () => switchAuditPanel('existentes'));
  $('#btn-mode-contado').addEventListener('click', () => switchAuditPanel('contado'));
```

- [ ] **Step 17: `beforeunload` — não finalizar mais a sessão compartilhada**

Find:
```js
// Handle page unload to save current session
window.addEventListener('beforeunload', () => {
  if (S.session) {
    finalizeSession();
  }
  if (S.cameraStream) stopCamera();
});
```

Replace:
```js
window.addEventListener('beforeunload', () => {
  if (S.cameraStream) stopCamera();
});
```

- [ ] **Step 18: Conferir que nenhuma referência aos nomes removidos sobrou**

Run (em `templates/index.html`):
```bash
grep -nE "auditMode|processScan\(|processCountScan|switchAuditMode|verificacao-panel|contagem-panel|popRegFilial|handleRegister|finalizeSession|reg-name|reg-email|reg-filial|btn-register|auth-register|stat-new|stat-dup|stat-total|btn-mode-verificacao|btn-mode-contagem" templates/index.html
```
Expected: nenhuma saída (nenhuma ocorrência). Se aparecer alguma linha, é uma referência que ficou para trás — corrija antes de continuar (provavelmente um call site que este plano não previu).

- [ ] **Step 19: Verificar sintaxe do JS**

Run:
```bash
python -c "
import re
content = open('templates/index.html', encoding='utf-8').read()
m = re.search(r'<script>(.*)</script>', content, re.S)
open('script_check_bipadores.js', 'w', encoding='utf-8').write(m.group(1))
"
node --check script_check_bipadores.js
rm -f script_check_bipadores.js
```
Expected: `node --check` sem saída (sucesso). Se falhar, a mensagem de erro traz o número da linha dentro do script extraído — corrija em `templates/index.html` antes de continuar.

- [ ] **Step 20: Commit**

```bash
git add templates/index.html
git commit -m "feat: fluxo unico de bipagem/contagem + aba Bipadores para admin"
```

---

### Task 3: Verificação manual no navegador (Playwright)

**Files:** nenhum (só verificação — sem harness de teste JS neste projeto, esta é a etapa de teste do front-end)

**Interfaces:**
- Consumes: servidor Flask rodando (`python server.py`) com as mudanças das Tasks 1 e 2, e a variável `ADMIN_PASSWORD` já configurada no `.env` local.

- [ ] **Step 1: Subir o servidor**

```bash
python server.py
```
Espere `Cache carregado do disco` (ou a sincronização completa se não houver `cache_data.json`) antes de continuar.

- [ ] **Step 2: Login de admin e cadastro de um bipador novo**

Abra `http://localhost:5000/`. No modal de login, clique em "Sou administrador", digite a senha (a mesma configurada em `ADMIN_PASSWORD` no `.env` — não peça para o usuário revelar o valor, apenas confirme que o login foi aceito) e entre. Confirme: cai direto na aba "Bipadores" (única visível na navegação, junto com "Sair"), lista os bipadores existentes agrupados por loja. Cadastre um bipador de teste numa loja (nome, email, loja no dropdown) e confirme que ele aparece na lista imediatamente após salvar.

- [ ] **Step 3: Dois bipadores da MESMA loja compartilham a sessão**

Faça logout do admin. Logue como o bipador recém-criado (nome+email, sem senha) — deve entrar direto na aba "Auditoria", sub-aba "Bipagem" ativa por padrão. Bipe um EAN conhecido; confirme que aparece "✅ PRODUTO EXISTENTE" e que a sub-aba "Produtos Existentes" mostra esse item. Faça logout, logue com outro bipador cadastrado **na mesma loja**, vá para Auditoria → Produtos Existentes: confirme que o item bipado pelo primeiro bipador já aparece ali (mesma sessão do dia, sem precisar bipar de novo).

- [ ] **Step 4: Regra "1º bipe existe, 2º+ conta"**

Ainda logado, bipe de novo o MESMO EAN do Step 3: confirme que o feedback muda para "📦 CONTANDO — ...: 1 un." (não mais um aviso de duplicado) e que o item passa a aparecer na sub-aba "Estoque Contado" com `qtd=1`. Bipe uma terceira vez o mesmo EAN: confirme `qtd=2`. Troque para a sub-aba "Produtos Existentes": confirme que o item continua lá (não some).

- [ ] **Step 5: Isolamento entre lojas**

Logue com um bipador de uma loja DIFERENTE. Confirme que a sub-aba "Produtos Existentes" começa vazia (não mostra o item bipado nas lojas anteriores) — sessões de lojas diferentes nunca se misturam.

- [ ] **Step 6: Bipador comum não vê a aba Bipadores; autocadastro sumiu**

Enquanto logado como bipador comum, confirme que a navegação NÃO mostra "Bipadores", e que o modal de login (após logout) não tem mais opção de "Cadastrar" — só "Entrar" e o link "Sou administrador".

- [ ] **Step 7: Reportar resultado**

Se todos os passos passarem, nenhuma ação adicional é necessária. Se algum falhar, corrija o problema específico em `templates/index.html` ou `server.py`, re-rode o passo afetado e reconfirme os passos anteriores que possam ter sido impactados antes de considerar esta tarefa concluída.

---

## Self-Review Notes

- Cobertura do spec: sessão por loja+dia (Task 1), remoção do autocadastro aberto + admin único com `hmac.compare_digest` (Task 1), fluxo unificado 1º-bipe-existe/2º-bipe-conta sem endpoints novos de contagem (Task 2, reaproveita `/api/audit/scan` e `/api/audit/count` já existentes), três sub-abas Bipagem/Produtos Existentes/Estoque Contado (Task 2), aba Bipadores agrupada por loja com formulário de criação (Task 2), remoção do `finalizeSession`/`/api/audit/session/finish` (Tasks 1 e 2, ambos os lados). Todas as seções do spec têm uma tarefa correspondente.
- Sem placeholders: todo passo tem código completo e executável.
- Consistência de nomes verificada entre as tarefas: `registerHit`, `switchAuditPanel`, `updateNavForRole`, `popAdminFilialSelect`, `loadBipadoresList`, `handleCreateBipador`, `handleAdminLogin` são usados de forma idêntica em todos os pontos do plano onde aparecem (HTML, wiring de eventos, chamadas entre funções).
- `ADMIN_PASSWORD` já está configurada localmente pelo usuário (não é uma tarefa deste plano) — a Task 1 assume que já existe no ambiente ao rodar os testes.
