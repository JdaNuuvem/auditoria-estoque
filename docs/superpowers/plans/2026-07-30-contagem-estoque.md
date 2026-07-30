# Contagem de Estoque Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Contagem" sub-tab alongside the existing "Verificação" audit flow, letting users tally physical unit counts per product (via repeated scans or manual entry) and see the divergence against the system's synced stock.

**Architecture:** Backend gains one new endpoint (`POST /api/audit/count`) that increments/decrements/sets a `qtd` field on the existing per-product entry inside the already-persisted audit session, and returns the system's stock quantity + divergence computed from the in-memory `CACHE["estoques"]`. Frontend adds a sub-tab switcher inside the existing Auditoria view; both sub-tabs share the same scan-input field and session state, differing only in what a scan/selection does.

**Tech Stack:** Flask (Python), vanilla JS/HTML (no build step, no JS test framework), pytest + Flask test client for backend tests.

## Global Constraints

- No new external dependencies (no JS framework, no new Python packages).
- `qtd` is per-product-per-session, stored inside the existing `session["encontrados"][productId]` dict — do not create a parallel data structure.
- Backend must never persist a partial/invalid count update — validate before writing (see spec's 400 rules).
- Barcode/camera scanning (`#scan-input`) always resolves by **EAN exact** only, in both sub-tabs — this must not change.
- Manual search (`#manual-search`) gains EAN → codproduto → NCM(list) resolution, in addition to its existing free-text substring dropdown (which must keep working).
- Spec source of truth: `docs/superpowers/specs/2026-07-30-contagem-estoque-design.md`.

---

### Task 1: Backend — `POST /api/audit/count` endpoint

**Files:**
- Modify: `server.py` (insert after the `audit_scan` function, currently ending at line 285 with `return jsonify({"ok": True, "dup": is_dup})`, before `@app.route("/api/audit/session/finish"...)` at line 288)
- Modify: `tests/test_local_persistence.py` (append new tests; reuses the existing `client` fixture already defined in that file)

**Interfaces:**
- Consumes: `server.CACHE["estoques"]` (list of dicts with `idproduto`, `filial`, `qtd` keys — already populated by the existing sync), `server._load_audit()` / `server._save_audit()` / `server._audit_lock` (already defined in `server.py`).
- Produces: `_estoque_sistema(product_id: int, filial_id) -> int` (module-level function in `server.py`) and the `/api/audit/count` route — both are used only by this task; no other task depends on new names from here beyond the route path `/api/audit/count`.

- [ ] **Step 1: Write the failing tests**

Open `tests/test_local_persistence.py` and add this block at the end of the file (after the existing `test_paginated_fetch_levanta_erro_em_resposta_nao_ok` function):

```python
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
```

**Note:** the last test mutates `server.CACHE["estoques"]` directly and resets it to `[]` at the end — this matches the existing (pre-established) pattern already used by `test_cache_save_e_load_do_disco_ida_e_volta` in this same file, which mutates `server.CACHE` without a fixture-level reset. Do not introduce a different mechanism here.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_local_persistence.py -k audit_count -v`
Expected: FAIL — every test gets a 404 from Flask (no route registered for `/api/audit/count`), since the endpoint doesn't exist yet.

- [ ] **Step 3: Implement `_estoque_sistema` and the `/api/audit/count` route**

In `server.py`, insert this immediately after the `audit_scan` function (right before the line `@app.route("/api/audit/session/finish", methods=["POST"])`):

```python
def _estoque_sistema(product_id, filial_id):
    total = 0
    for e in CACHE.get("estoques", []):
        if e.get("idproduto") == product_id and e.get("filial") == filial_id:
            total += e.get("qtd") or 0
    return total


@app.route("/api/audit/count", methods=["POST"])
def audit_count():
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId")
    product_id = data.get("productId")
    ean = data.get("ean") or ""
    descricao = data.get("descricao") or ""
    has_delta = "delta" in data
    has_qtd = "qtd" in data

    if not session_id or product_id is None:
        return jsonify({"ok": False, "error": "sessionId e productId sao obrigatorios."}), 400
    if has_delta and has_qtd:
        return jsonify({"ok": False, "error": "Informe apenas um de delta ou qtd, nao os dois."}), 400
    if not has_delta and not has_qtd:
        return jsonify({"ok": False, "error": "Informe delta ou qtd."}), 400
    if has_delta and data.get("delta") not in (1, -1):
        return jsonify({"ok": False, "error": "delta deve ser 1 ou -1."}), 400
    if has_qtd and (not isinstance(data.get("qtd"), int) or data.get("qtd") < 0):
        return jsonify({"ok": False, "error": "qtd deve ser um inteiro >= 0."}), 400

    with _audit_lock:
        sessions = _load_audit()
        session = sessions.get(session_id)
        if not session:
            return jsonify({"ok": False, "error": "Sessao de auditoria nao encontrada."}), 404
        pid = str(product_id)
        entry = session["encontrados"].get(pid)
        if not entry:
            entry = session["encontrados"][pid] = {
                "ean": ean, "descricao": descricao,
                "scannedAt": datetime.now().strftime("%H:%M:%S"), "qtd": 0,
            }
        if has_qtd:
            entry["qtd"] = data["qtd"]
        else:
            entry["qtd"] = max(0, entry.get("qtd", 0) + data["delta"])
        _save_audit(sessions)
        qtd_sistema = _estoque_sistema(int(product_id), session["filialId"])

    return jsonify({
        "ok": True,
        "qtd": entry["qtd"],
        "qtdSistema": qtd_sistema,
        "diferenca": entry["qtd"] - qtd_sistema,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_local_persistence.py -v`
Expected: PASS — all tests in the file, including the new `test_audit_count_*` ones (this also re-confirms no regression in the existing audit/cache tests).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_local_persistence.py
git commit -m "feat: adiciona endpoint /api/audit/count para contagem de estoque"
```

---

### Task 2: Frontend — sub-abas Verificação/Contagem

**Files:**
- Modify: `templates/index.html` (HTML structure inside `#audit-view`, JS state in `S`, `buildIndexes()`, `findByText()`, new functions, event listener wiring)

**Interfaces:**
- Consumes: `POST /api/audit/count` from Task 1 (body `{sessionId, productId, ean, descricao, delta}` or `{..., qtd}`, response `{ok, qtd, qtdSistema, diferenca}`), and existing globals `S`, `$`, `fmt`, `toast`, `S.session.encontrados`, `S.productsMap`, `findByEan`, `markFound`, `showProductDetails`, `updateScanStats`, `updateAuditUI`, `updateDashboardUI`, `playBeep`, `playBuzzer`, `showScanFeedback`.
- Produces: `S.auditMode` (`'verificacao'` default or `'contagem'`), `resolveProduct(code)`, `applyCountDelta(productId, delta)`, `setCountForProduct(productId, qtd)`, `renderCountList()`, `switchAuditMode(mode)` — no later task depends on these (this is the last code task).

- [ ] **Step 1: Add `auditMode` to state and `productsByCodigo` index**

In `templates/index.html`, find this block (around line 290):

```js
let S = {
  user: null, // { name, email, filialId } - logged-in user
  view: 'loading',
  dataReady: false,
  products: [],
  productsMap: {},
  productsByEan: {},
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

Replace it with:

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

- [ ] **Step 2: Extend `buildIndexes()` to also index by `codproduto`**

Find (around line 425):

```js
function buildIndexes() {
  S.productsMap = {};
  S.productsByEan = {};
  for (const p of S.products) {
    S.productsMap[p.id] = p;
    const e = fmt(p.ean);
    if (!e) continue;
    if (!S.productsByEan[e]) S.productsByEan[e] = [];
    S.productsByEan[e].push(p);
  }
}
```

Replace with:

```js
function buildIndexes() {
  S.productsMap = {};
  S.productsByEan = {};
  S.productsByCodigo = {};
  for (const p of S.products) {
    S.productsMap[p.id] = p;
    const e = fmt(p.ean);
    if (e) {
      if (!S.productsByEan[e]) S.productsByEan[e] = [];
      S.productsByEan[e].push(p);
    }
    const c = fmt(p.codproduto);
    if (c) {
      if (!S.productsByCodigo[c]) S.productsByCodigo[c] = [];
      S.productsByCodigo[c].push(p);
    }
  }
}
```

- [ ] **Step 3: Add NCM to the free-text manual search**

Find (around line 515):

```js
function findByText(q) {
  const ql = q.toLowerCase().trim();
  if (!ql) return [];
  return S.products.filter(p => {
    return fmt(p.descricao).toLowerCase().includes(ql)
      || fmt(p.codproduto).toLowerCase().includes(ql)
      || fmt(p.ean).includes(ql)
      || fmt(p.referencia).toLowerCase().includes(ql)
      || fmt(p.modelo).toLowerCase().includes(ql);
  }).slice(0, 50);
}
```

Replace with:

```js
function findByText(q) {
  const ql = q.toLowerCase().trim();
  if (!ql) return [];
  return S.products.filter(p => {
    return fmt(p.descricao).toLowerCase().includes(ql)
      || fmt(p.codproduto).toLowerCase().includes(ql)
      || fmt(p.ean).includes(ql)
      || fmt(p.referencia).toLowerCase().includes(ql)
      || fmt(p.modelo).toLowerCase().includes(ql)
      || fmt(p.ncm).includes(ql);
  }).slice(0, 50);
}

// EAN exato -> codproduto exato -> lista por NCM (nunca resolve NCM direto a 1 produto)
function resolveProduct(code) {
  const c = fmt(code);
  if (!c) return { type: 'none', matches: [] };
  const byEan = findByEan(c);
  if (byEan && byEan.length) return { type: 'ean', matches: byEan };
  const byCodigo = S.productsByCodigo[c];
  if (byCodigo && byCodigo.length) return { type: 'codproduto', matches: byCodigo };
  const byNcm = S.products.filter(p => fmt(p.ncm) === c);
  if (byNcm.length) return { type: 'ncm', matches: byNcm };
  return { type: 'none', matches: [] };
}
```

- [ ] **Step 4: Replace the `#audit-view` HTML with sub-tabs**

Find this block (around line 194-221):

```html
    <!-- AUDIT -->
    <div id="audit-view" style="display:none">
      <div class="audit-layout">
        <div class="scan-zone">
          <input id="scan-input" class="scan-input" placeholder="Bipe o código de barras aqui..." autocomplete="off" inputmode="numeric">
          <div class="scan-actions">
            <button class="btn-p" id="btn-camera">Câmera</button>
            <input id="manual-search" placeholder="Buscar por nome, SKU ou código..." style="flex:1;min-width:150px">
            <button class="btn-o" id="btn-print-label">Imprimir Etiqueta</button>
          </div>
          <div id="scan-feedback"></div>
          <div id="scan-stats" style="display:flex;gap:16px;justify-content:center;padding:8px;font-size:.85rem;font-weight:600">
            <span style="color:var(--s600)">🆕 Novos: <strong id="stat-new">0</strong></span>
            <span style="color:var(--w600)">🔄 Duplicados: <strong id="stat-dup">0</strong></span>
            <span style="color:var(--p600)">📦 Total: <strong id="stat-total">0</strong></span>
          </div>
          <div id="search-results" style="display:none"></div>
          <h4 style="margin-top:8px">Últimos bipados (<span id="found-count">0</span> encontrados)</h4>
          <div class="recent-scans" id="recent-scans-list"></div>
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

Replace with:

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

- [ ] **Step 5: Add mode-aware count functions and update `selectManualProduct`**

Find (around line 861-902):

```js
/* ===== MANUAL SEARCH ===== */
const doSearch = debounce((q) => {
  const results = findByText(q);
  const el = $('#search-results');
  if (!q || results.length === 0) {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'block';
  el.style.maxHeight = '300px';
  el.style.overflowY = 'auto';
  el.style.background = 'var(--surface)';
  el.style.border = '1px solid var(--border)';
  el.style.borderRadius = 'var(--radius)';
  el.style.padding = '8px';
  el.innerHTML = results.slice(0, 20).map(p => `
    <div style="padding:8px;cursor:pointer;border-bottom:1px solid var(--border);font-size:.85rem"
         onclick="selectManualProduct(${p.id})">
      <strong>${fmt(p.codproduto)}</strong> - ${fmt(p.descricao)}
      <span style="color:var(--muted);font-size:.75rem">EAN: ${fmt(p.ean) || 'sem código'}</span>
    </div>
  `).join('');
}, 300);

function selectManualProduct(productId) {
  const p = S.productsMap[productId];
  if (!p) return;
  const result = markFound(productId, fmt(p.ean) || 'manual', fmt(p.descricao));
  if (result === 'dup') {
    showScanFeedback('⚠️ PRODUTO JÁ REGISTRADO — ' + p.descricao, 'dup');
    playBuzzer();
  } else {
    showScanFeedback('✅ NOVO PRODUTO REGISTRADO (manual) — ' + p.descricao, 'ok');
    playBeep();
  }
  showProductDetails(p);
  updateScanStats();
  updateAuditUI();
  updateDashboardUI();
  $('#search-results').style.display = 'none';
  $('#manual-search').value = '';
}
```

Replace with:

```js
/* ===== MANUAL SEARCH ===== */
function renderSearchResults(products) {
  const el = $('#search-results');
  if (!products || products.length === 0) {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'block';
  el.style.maxHeight = '300px';
  el.style.overflowY = 'auto';
  el.style.background = 'var(--surface)';
  el.style.border = '1px solid var(--border)';
  el.style.borderRadius = 'var(--radius)';
  el.style.padding = '8px';
  el.innerHTML = products.slice(0, 20).map(p => `
    <div style="padding:8px;cursor:pointer;border-bottom:1px solid var(--border);font-size:.85rem"
         onclick="selectManualProduct(${p.id})">
      <strong>${fmt(p.codproduto)}</strong> - ${fmt(p.descricao)}
      <span style="color:var(--muted);font-size:.75rem">EAN: ${fmt(p.ean) || 'sem código'}</span>
    </div>
  `).join('');
}

const doSearch = debounce((q) => {
  if (!q) { $('#search-results').style.display = 'none'; return; }
  renderSearchResults(findByText(q));
}, 300);

function selectManualProduct(productId) {
  const p = S.productsMap[productId];
  if (!p) return;
  if (S.auditMode === 'contagem') {
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

/* ===== CONTAGEM ===== */
function applyCountDelta(productId, delta) {
  if (!S.session) return;
  const p = S.productsMap[productId];
  let entry = S.session.encontrados[productId];
  if (!entry) {
    entry = S.session.encontrados[productId] = {
      ean: fmt(p && p.ean) || '', descricao: fmt(p && p.descricao) || '',
      scannedAt: new Date().toLocaleTimeString('pt-BR'), qtd: 0,
    };
  }
  entry.qtd = Math.max(0, (entry.qtd || 0) + delta);
  renderCountList();
  syncCountToServer(productId, entry.ean, entry.descricao, { delta });
}

function setCountForProduct(productId, qtd) {
  if (!S.session) return;
  const p = S.productsMap[productId];
  let entry = S.session.encontrados[productId];
  if (!entry) {
    entry = S.session.encontrados[productId] = {
      ean: fmt(p && p.ean) || '', descricao: fmt(p && p.descricao) || '',
      scannedAt: new Date().toLocaleTimeString('pt-BR'), qtd: 0,
    };
  }
  entry.qtd = Math.max(0, qtd);
  renderCountList();
  syncCountToServer(productId, entry.ean, entry.descricao, { qtd: entry.qtd });
}

function syncCountToServer(productId, ean, descricao, payload) {
  fetch('/api/audit/count', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId: S.session.id, productId, ean, descricao, ...payload }),
  }).then(r => r.json()).then(r => {
    if (!r.ok) { toast('Aviso: falha ao salvar contagem — ' + r.error, 'warn'); return; }
    const entry = S.session.encontrados[productId];
    if (entry) {
      entry.qtd = r.qtd;
      entry.qtdSistema = r.qtdSistema;
      entry.diferenca = r.diferenca;
      renderCountList();
    }
  }).catch(() => toast('Aviso: sem conexão para salvar contagem.', 'warn'));
}

function renderCountList() {
  if (!S.session) return;
  const entries = Object.entries(S.session.encontrados).filter(([, r]) => (r.qtd || 0) > 0);
  $('#count-total').textContent = entries.length;
  $('#count-list').innerHTML = entries.map(([id, r]) => `
    <div class="recent-row" style="flex-direction:column;align-items:stretch;gap:6px;cursor:default">
      <div style="display:flex;justify-content:space-between">
        <strong>${r.descricao}</strong>
        <span style="color:var(--muted);font-size:.75rem">${r.ean}</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <button class="btn-o btn-sm" onclick="applyCountDelta(${id}, -1)">-1</button>
        <input type="number" min="0" value="${r.qtd}" style="width:70px;text-align:center"
               onchange="setCountForProduct(${id}, parseInt(this.value)||0)">
        <button class="btn-o btn-sm" onclick="applyCountDelta(${id}, 1)">+1</button>
        <span style="font-size:.8rem;color:var(--muted)">
          Sistema: ${r.qtdSistema != null ? r.qtdSistema : '...'} | Diferença: ${r.diferenca != null ? (r.diferenca > 0 ? '+' : '') + r.diferenca : '...'}
        </span>
      </div>
    </div>
  `).join('');
}

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

- [ ] **Step 6: Wire the mode-dependent scan-input handler**

Find (around line 777-788):

```js
function initScannerInput() {
  const inp = $('#scan-input');
  if (!inp) return;
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const val = inp.value.trim();
      inp.value = '';
      if (val) processScan(val);
    }
  });
}
```

Replace with:

```js
function initScannerInput() {
  const inp = $('#scan-input');
  if (!inp) return;
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const val = inp.value.trim();
      inp.value = '';
      if (!val) return;
      if (S.auditMode === 'contagem') processCountScan(val);
      else processScan(val);
    }
  });
}
```

- [ ] **Step 7: Wire the mode-tab buttons and manual-search Enter key**

Find (around line 1323-1324):

```js
  // Event: manual search
  $('#manual-search').addEventListener('input', (e) => doSearch(e.target.value));
```

Replace with:

```js
  // Event: manual search
  $('#manual-search').addEventListener('input', (e) => doSearch(e.target.value));
  $('#manual-search').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const val = $('#manual-search').value.trim();
    if (!val) return;
    const resolved = resolveProduct(val);
    if (resolved.type === 'none') {
      showScanFeedback('❌ CÓDIGO NÃO ENCONTRADO NO CATÁLOGO', 'err');
      return;
    }
    if (resolved.type === 'ncm') {
      renderSearchResults(resolved.matches);
      return;
    }
    selectManualProduct(resolved.matches[0].id);
  });

  // Event: audit mode tabs
  $('#btn-mode-verificacao').addEventListener('click', () => switchAuditMode('verificacao'));
  $('#btn-mode-contagem').addEventListener('click', () => switchAuditMode('contagem'));
```

- [ ] **Step 8: Verify JS syntax**

Run:
```bash
python -c "
import re
content = open('templates/index.html', encoding='utf-8').read()
m = re.search(r'<script>(.*)</script>', content, re.S)
open('/tmp/_check.js', 'w', encoding='utf-8').write(m.group(1))
"
node --check /tmp/_check.js
```
Expected: no output (exit code 0). If it fails, the error message gives a line number inside the extracted script — fix the syntax issue in `templates/index.html` before continuing.

- [ ] **Step 9: Commit**

```bash
git add templates/index.html
git commit -m "feat: adiciona aba Contagem de estoque com bipagem e entrada manual"
```

---

### Task 3: Verificação manual no navegador (Playwright)

**Files:** none (verification only — this project has no JS test harness, so this task is the front-end's test cycle)

**Interfaces:**
- Consumes: the running Flask server (`python server.py`) and the changes from Tasks 1–2.

- [ ] **Step 1: Start the server locally**

```bash
python server.py
```
Wait for `Cache carregado do disco` (or full sync if no `cache_data.json` exists yet) before proceeding — login is blocked until the cache is ready.

- [ ] **Step 2: Log in and open Auditoria**

Navigate to `http://localhost:5000/`, log in with an existing user (or register one), and open the "Auditoria" tab. Confirm the new **[Verificação] [Contagem]** sub-tabs appear above the scan input, with "Verificação" active by default and the existing "Últimos bipados" list visible.

- [ ] **Step 3: Verify Verificação still works unchanged**

Type a known EAN into `#scan-input` and press Enter. Confirm: scan feedback shows "NOVO PRODUTO REGISTRADO", the product appears in "Últimos bipados", and scanning the same EAN again shows "PRODUTO JÁ REGISTRADO".

- [ ] **Step 4: Switch to Contagem and scan**

Click "Contagem". Confirm the "Últimos bipados" panel disappears and an empty "Contagem desta sessão (0 produtos)" panel appears. Scan the same EAN from Step 3 three times (via `#scan-input` + Enter). Confirm: scan feedback shows "— contagem: 1", then "— contagem: 2", then "— contagem: 3", and a row appears in the Contagem list showing `[-1] [ 3 ] [+1]` plus "Sistema: X | Diferença: ±Y" (values should appear within ~1s of the last scan, once the server response for the last count comes back).

- [ ] **Step 5: Verify manual override and correction controls**

In the Contagem list row from Step 4, click into the quantity input, type `10`, and press Tab/blur. Confirm the row updates to `qtd=10` (not `13`) and "Sistema"/"Diferença" update accordingly. Click `-1` once; confirm it goes to `9`. Click `-1` nine more times; confirm it stops at `0` and never goes negative.

- [ ] **Step 6: Verify NCM manual search**

Note the NCM of any product visible in the product panel (from Step 3's scan). Type that NCM into `#manual-search` and press Enter. Confirm a dropdown list appears with multiple products (not auto-resolving to one). Click one of them; confirm it registers as found (Verificação mode) or increments its count (Contagem mode), matching whichever sub-tab is currently active.

- [ ] **Step 7: Verify persistence across reload**

Reload the page, log in again, reopen Auditoria, switch to Contagem. Confirm the product counted in Step 5 still shows `qtd=0` (from decrementing) is gone from the list (list only shows `qtd > 0` — re-scan it once to bring it back to `qtd=1` and confirm it reappears), and that a fresh count made just before reload is still present after reload (server-persisted, not lost).

- [ ] **Step 8: Report result**

If all checks pass, no further action needed. If any check fails, fix the specific issue in `templates/index.html` or `server.py`, re-run the affected step, and re-verify Steps 3–7 that could have been affected before considering this task done.

---

## Self-Review Notes

- Spec coverage: unified data model (Task 1's `qtd` field lives inside `encontrados`), delta/qtd contract with 400s (Task 1), `qtdSistema`/`diferenca` via sum (Task 1), EAN-only scanning unchanged (Task 2 Step 6 keeps `processScan` untouched, only adds a parallel `processCountScan`), manual search EAN→codproduto→NCM-list (Task 2 Steps 3 & 7), +1/-1/edit correction UI (Task 2 Step 5's `renderCountList`), sub-tabs sharing session (Task 2 Step 4). All spec sections are covered.
- No placeholders: every step has full, runnable code.
- Type consistency checked: `applyCountDelta`, `setCountForProduct`, `syncCountToServer`, `renderCountList`, `resolveProduct`, `switchAuditMode`, `processCountScan` are named identically everywhere they're defined and called across Task 2's steps.
