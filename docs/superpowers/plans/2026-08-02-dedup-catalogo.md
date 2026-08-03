# Deduplicação de Catálogo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detectar automaticamente cadastros de produto duplicados por loja (EAN igual, ou NCM+marca+descrição parecida via rapidfuzz), deixar o admin revisar/aprovar/rejeitar, e persistir localmente os grupos confirmados — sem escrever na API i9logic (que é somente leitura).

**Architecture:** Backend adiciona um arquivo de persistência novo (`dedup_groups.json`, mesmo padrão de `users.json`/`audit_sessions.json`: dict + lock de thread) e 5 rotas Flask (4 admin, 1 pública) que leem `CACHE["produtos"]`/`CACHE["estoques"]` já sincronizados — nada de chamada nova à API externa. Front-end adiciona uma view nova só-admin ("Deduplicação"), reaproveitando os padrões já existentes de autenticação de admin, seletor de filial e listener delegado por `data-*` attribute.

**Tech Stack:** Flask (já existente), `rapidfuzz` (nova dependência, extensão C pura, sem peso de ML/IA — comparação de strings robusta a reordenação de palavras).

## Global Constraints

- `rapidfuzz==3.14.5` é a única dependência nova, adicionada a `requirements.txt`.
- Thresholds de confiança do algoritmo (exatos, do spec): `token_sort_ratio` da descrição normalizada ≥ 90 → confiança "alta"; ≥ 75 → "media"; ≥ 60 → "baixa"; abaixo de 60 → descartado (não vira candidato). EAN idêntico entre 2+ produtos → confiança "alta" direto, independente do score de descrição.
- `signature` de um grupo candidato é determinística: IDs de produto ordenados numericamente, unidos por `"-"` (ex: `"123-456"`). Serve tanto para persistir a decisão quanto para o cliente reconstruir os `memberProductIds` sem precisar reenviar a lista inteira.
- Todas as rotas `/api/admin/dedup/*` exigem `adminPassword` no corpo, validada com `_admin_password_ok` (já existe em `server.py`) e protegida pelo mesmo limitador de tentativas `_admin_login_attempts`/`_rate_limited`/`_record_failed_attempt` (já existe, compartilhado com `admin_login`/`admin_create_bipador`/`admin_reset_bipador_password` — reaproveitar exatamente o mesmo bucket, não criar um novo).
- `GET /api/dedup/groups` é pública (sem `adminPassword`), mesmo padrão de `GET /api/auth/users` — não expõe nada sensível.
- Decisão de unificar (ou rejeitar) é **independente por loja** — a mesma dupla de cadastros pode estar aprovada numa filial e não decidida (ou rejeitada) em outra. `dedup_groups.json` é `{filialId (string): {signature: {...}}}`.
- Candidato só é sugerido para uma filial se ela tiver estoque do produto (`CACHE["estoques"]` com `filial == filialId`).
- Qualquer descrição/marca/EAN/SKU de produto interpolado em `innerHTML` no front-end passa por `escapeHtml()` (já existe em `templates/index.html`) — nunca interpolação direta.
- Nada de escrita na API i9logic (`requests.post/put/patch/delete` para `API_BASE` é proibido neste plano — o app é e continua somente leitura frente à i9logic).
- Não editar `deploy-coolify.sh` (script legado, fora de uso, confirmado pelo usuário em feature anterior).

---

### Task 1: Persistência local + normalização + assinatura de grupo

**Files:**
- Modify: `server.py` (novas funções, perto de `_load_users`/`_save_users`, por volta da linha 390 hoje — a localização exata pode ter mudado, procure por `def _load_users():` como âncora)
- Test: `tests/test_local_persistence.py`

**Interfaces:**
- Produz para as próximas tasks: `DEDUP_FILE` (str, caminho do arquivo), `_dedup_lock` (`threading.Lock`), `_load_dedup() -> dict`, `_save_dedup(data: dict) -> None`, `_normalize_descricao(text) -> str`, `_group_signature(product_ids: list[int]) -> str`, `_signature_to_ids(signature: str) -> list[int]` (levanta `ValueError` se algum segmento não for numérico).

- [ ] **Step 1: Adicionar `DEDUP_FILE` à fixture de teste**

Abra `tests/test_local_persistence.py` e localize a fixture `client` (topo do arquivo, começa com `@pytest.fixture` seguido de `def client(tmp_path, monkeypatch):`). Adicione uma linha de `monkeypatch.setattr` para `DEDUP_FILE`, no mesmo padrão das linhas já existentes para `AUDIT_FILE`/`CACHE_FILE`/`USERS_FILE`:

```python
    monkeypatch.setattr(server, "DEDUP_FILE", str(tmp_path / "dedup_groups.json"))
```

Coloque essa linha logo depois de `monkeypatch.setattr(server, "USERS_FILE", str(tmp_path / "users.json"))` e antes de `monkeypatch.setattr(server, "_admin_login_attempts", {})`.

- [ ] **Step 2: Escrever os testes que falham**

Adicione ao final de `tests/test_local_persistence.py`:

```python
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
```

- [ ] **Step 3: Rodar os testes para confirmar que falham**

Run: `python -m pytest tests/test_local_persistence.py -k "normalize_descricao or group_signature or signature_to_ids or load_dedup or save_e_load_dedup" -v`
Expected: FAIL com `AttributeError: module 'server' has no attribute '_normalize_descricao'` (ou similar para as outras funções — todas ainda não existem).

- [ ] **Step 4: Implementar em `server.py`**

Adicione `import unicodedata` ao topo do arquivo, junto dos outros imports (depois de `import threading`, antes de `from datetime import date, datetime`):

```python
import unicodedata
```

Localize `def _load_users():` em `server.py` (por volta da linha 390) e adicione, **antes** dela, as novas constantes e funções:

```python
DEDUP_FILE = os.path.join(DATA_DIR, "dedup_groups.json")
_dedup_lock = threading.Lock()


def _normalize_descricao(text):
    text = str(text or "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    return " ".join(text.split())


def _group_signature(product_ids):
    return "-".join(str(pid) for pid in sorted(product_ids))


def _signature_to_ids(signature):
    return [int(x) for x in signature.split("-")]


def _load_dedup():
    try:
        with open(DEDUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_dedup(data):
    with open(DEDUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 5: Rodar os testes de novo, confirmar que passam**

Run: `python -m pytest tests/test_local_persistence.py -k "normalize_descricao or group_signature or signature_to_ids or load_dedup or save_e_load_dedup" -v`
Expected: PASS (7 testes).

- [ ] **Step 6: Rodar a suite inteira, confirmar que nada quebrou**

Run: `python -m pytest tests/ -q`
Expected: todos os testes já existentes continuam passando (63 antes desta task), mais os 7 novos.

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_local_persistence.py
git commit -m "feat: adiciona persistencia local e normalizacao para deduplicacao de catalogo"
```

---

### Task 2: Algoritmo de detecção de duplicatas

**Files:**
- Modify: `server.py` (nova classe `_UnionFind` e função `_find_duplicate_candidates`, logo depois das funções da Task 1)
- Modify: `requirements.txt` (adicionar `rapidfuzz==3.14.5`)
- Test: `tests/test_local_persistence.py`

**Interfaces:**
- Consome: `_normalize_descricao`, `_group_signature`, `_load_dedup` (Task 1); `_estoque_sistema(product_id, filial_id) -> int` (já existe em `server.py`, por volta da linha 329); `CACHE["produtos"]` (lista de dicts com `id`, `descricao`, `ean`, `ncm`, `marca`, `codproduto`) e `CACHE["estoques"]` (lista de dicts com `idproduto`, `filial`, `qtd`) — ambos já existentes.
- Produz para a Task 3: `_find_duplicate_candidates(filial_id: int) -> list[dict]`, cada item no formato:
  ```python
  {
      "signature": "123-456",
      "memberProductIds": [123, 456],
      "confidence": "alta" | "media" | "baixa",
      "signals": ["ean_igual"],  # lista ordenada, pode ter "ean_igual", "ncm_marca_iguais", "descricao_muito_similar"
      "members": [
          {"id": 123, "descricao": "...", "ean": "...", "ncm": "...", "marca": "...", "codproduto": "...", "estoqueFilial": 5},
          ...
      ],
  }
  ```
  Ordenado com confiança "alta" primeiro, depois "media", depois "baixa" (dentro do mesmo nível, grupos maiores primeiro). Exclui candidatos cuja `signature` já tem decisão (`approved` ou `rejected`) persistida para aquela filial.

- [ ] **Step 1: Adicionar rapidfuzz ao requirements.txt**

Adicione ao final de `requirements.txt`:

```
rapidfuzz==3.14.5
```

Instale no ambiente local: `pip install rapidfuzz==3.14.5`

- [ ] **Step 2: Escrever os testes que falham**

Adicione ao final de `tests/test_local_persistence.py`:

```python
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
```

- [ ] **Step 3: Rodar os testes para confirmar que falham**

Run: `python -m pytest tests/test_local_persistence.py -k "find_duplicate_candidates" -v`
Expected: FAIL com `AttributeError: module 'server' has no attribute '_find_duplicate_candidates'`.

- [ ] **Step 4: Implementar em `server.py`**

Adicione `from rapidfuzz import fuzz` ao topo do arquivo, junto dos outros imports de terceiros (depois de `from werkzeug.security import generate_password_hash, check_password_hash`):

```python
from rapidfuzz import fuzz
```

Adicione, logo depois de `_save_dedup` (final da Task 1):

```python
class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _find_duplicate_candidates(filial_id):
    stocked_ids = {e.get("idproduto") for e in CACHE.get("estoques", []) if e.get("filial") == filial_id}
    products = [p for p in CACHE.get("produtos", []) if p.get("id") in stocked_ids]
    by_id = {p["id"]: p for p in products}
    if len(by_id) < 2:
        return []

    decided = _load_dedup().get(str(filial_id), {})

    uf = _UnionFind(by_id.keys())
    pair_scores = {}

    by_ean = {}
    for p in products:
        ean = str(p.get("ean") or "").strip()
        if ean:
            by_ean.setdefault(ean, []).append(p["id"])
    for ids in by_ean.values():
        if len(ids) < 2:
            continue
        base = ids[0]
        for other in ids[1:]:
            uf.union(base, other)

    by_ncm_marca = {}
    for p in products:
        ncm = str(p.get("ncm") or "").strip()
        marca = str(p.get("marca") or "").strip()
        if ncm and marca:
            by_ncm_marca.setdefault((ncm, marca), []).append(p)

    for group in by_ncm_marca.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                score = fuzz.token_sort_ratio(
                    _normalize_descricao(a.get("descricao")),
                    _normalize_descricao(b.get("descricao")),
                )
                if score >= 60:
                    uf.union(a["id"], b["id"])
                    key = tuple(sorted((a["id"], b["id"])))
                    pair_scores[key] = score

    components = {}
    for pid in by_id:
        root = uf.find(pid)
        components.setdefault(root, []).append(pid)

    candidates = []
    for member_ids in components.values():
        if len(member_ids) < 2:
            continue
        signature = _group_signature(member_ids)
        if signature in decided:
            continue

        signals_set = set()
        min_desc_score = None
        has_ean_match = False
        for i in range(len(member_ids)):
            for j in range(i + 1, len(member_ids)):
                a_id, b_id = member_ids[i], member_ids[j]
                a, b = by_id[a_id], by_id[b_id]
                ean_a, ean_b = str(a.get("ean") or "").strip(), str(b.get("ean") or "").strip()
                if ean_a and ean_a == ean_b:
                    has_ean_match = True
                    signals_set.add("ean_igual")
                key = tuple(sorted((a_id, b_id)))
                if key in pair_scores:
                    signals_set.add("ncm_marca_iguais")
                    score = pair_scores[key]
                    min_desc_score = score if min_desc_score is None else min(min_desc_score, score)

        if has_ean_match:
            confidence = "alta"
        elif min_desc_score is not None and min_desc_score >= 90:
            confidence = "alta"
            signals_set.add("descricao_muito_similar")
        elif min_desc_score is not None and min_desc_score >= 75:
            confidence = "media"
        else:
            confidence = "baixa"

        members = []
        for pid in sorted(member_ids):
            p = by_id[pid]
            members.append({
                "id": pid,
                "descricao": p.get("descricao"),
                "ean": p.get("ean"),
                "ncm": p.get("ncm"),
                "marca": p.get("marca"),
                "codproduto": p.get("codproduto"),
                "estoqueFilial": _estoque_sistema(pid, filial_id),
            })

        candidates.append({
            "signature": signature,
            "memberProductIds": sorted(member_ids),
            "confidence": confidence,
            "signals": sorted(signals_set),
            "members": members,
        })

    order = {"alta": 0, "media": 1, "baixa": 2}
    candidates.sort(key=lambda c: (order[c["confidence"]], -len(c["members"])))
    return candidates
```

- [ ] **Step 5: Rodar os testes de novo, confirmar que passam**

Run: `python -m pytest tests/test_local_persistence.py -k "find_duplicate_candidates" -v`
Expected: PASS (5 testes).

- [ ] **Step 6: Rodar a suite inteira**

Run: `python -m pytest tests/ -q`
Expected: todos os testes passando (70 no total até aqui).

- [ ] **Step 7: Commit**

```bash
git add server.py requirements.txt tests/test_local_persistence.py
git commit -m "feat: algoritmo de deteccao de duplicatas por EAN e similaridade de descricao"
```

---

### Task 3: Endpoints de administração e consulta

**Files:**
- Modify: `server.py` (5 rotas novas, logo depois de `admin_reset_bipador_password` — por volta da linha 478 hoje, procure por `@app.route("/api/admin/bipadores/reset-password"` como âncora e adicione depois do `return jsonify({"ok": True})` dela)
- Test: `tests/test_local_persistence.py`

**Interfaces:**
- Consome: `_admin_password_ok`, `_rate_limited`, `_record_failed_attempt`, `_admin_login_attempts` (já existentes); `_find_duplicate_candidates`, `_signature_to_ids`, `_load_dedup`, `_save_dedup`, `_dedup_lock`, `_estoque_sistema` (Tasks 1-2).
- Produz: rotas HTTP `POST /api/admin/dedup/analyze`, `POST /api/admin/dedup/confirm`, `POST /api/admin/dedup/bulk-confirm`, `POST /api/admin/dedup/reject`, `GET /api/dedup/groups` — consumidas pelo front-end na Task 4.

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao final de `tests/test_local_persistence.py`:

```python
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
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `python -m pytest tests/test_local_persistence.py -k "dedup_analyze or dedup_confirm or dedup_bulk_confirm or dedup_reject or dedup_groups or dedup_decisoes" -v`
Expected: FAIL com 404 (rotas ainda não existem) nas asserções de status code.

- [ ] **Step 3: Implementar as rotas em `server.py`**

Adicione, logo depois da rota `admin_reset_bipador_password` (depois do `return jsonify({"ok": True})` final dela, antes de `@app.route("/api/auth/login"`):

```python
@app.route("/api/admin/dedup/analyze", methods=["POST"])
def dedup_analyze():
    ip = request.remote_addr or "unknown"
    limited, count, window_start = _rate_limited(_admin_login_attempts, ip)
    if limited:
        return jsonify({"ok": False, "error": "Muitas tentativas. Tente novamente em alguns minutos."}), 429

    data = request.get_json(silent=True) or {}
    admin_password = data.get("adminPassword") or ""
    if not _admin_password_ok(admin_password):
        _record_failed_attempt(_admin_login_attempts, ip, count, window_start)
        return jsonify({"ok": False, "error": "Senha de administrador incorreta."}), 403
    _admin_login_attempts.pop(ip, None)

    filial_id = data.get("filialId")
    if filial_id is None:
        return jsonify({"ok": False, "error": "filialId e obrigatorio."}), 400
    candidates = _find_duplicate_candidates(filial_id)
    return jsonify({"ok": True, "candidates": candidates})


@app.route("/api/admin/dedup/confirm", methods=["POST"])
def dedup_confirm():
    ip = request.remote_addr or "unknown"
    limited, count, window_start = _rate_limited(_admin_login_attempts, ip)
    if limited:
        return jsonify({"ok": False, "error": "Muitas tentativas. Tente novamente em alguns minutos."}), 429

    data = request.get_json(silent=True) or {}
    admin_password = data.get("adminPassword") or ""
    if not _admin_password_ok(admin_password):
        _record_failed_attempt(_admin_login_attempts, ip, count, window_start)
        return jsonify({"ok": False, "error": "Senha de administrador incorreta."}), 403
    _admin_login_attempts.pop(ip, None)

    filial_id = data.get("filialId")
    signature = data.get("signature")
    canonical_id = data.get("canonicalProductId")
    if filial_id is None or not signature or canonical_id is None:
        return jsonify({"ok": False, "error": "filialId, signature e canonicalProductId sao obrigatorios."}), 400
    try:
        member_ids = _signature_to_ids(signature)
    except ValueError:
        return jsonify({"ok": False, "error": "signature invalida."}), 400
    if canonical_id not in member_ids:
        return jsonify({"ok": False, "error": "canonicalProductId deve ser um dos membros do grupo."}), 400

    with _dedup_lock:
        dedup = _load_dedup()
        filial_groups = dedup.setdefault(str(filial_id), {})
        filial_groups[signature] = {
            "status": "approved",
            "memberProductIds": member_ids,
            "canonicalProductId": canonical_id,
            "decidedAt": datetime.now().isoformat(),
        }
        _save_dedup(dedup)
    return jsonify({"ok": True})


@app.route("/api/admin/dedup/bulk-confirm", methods=["POST"])
def dedup_bulk_confirm():
    ip = request.remote_addr or "unknown"
    limited, count, window_start = _rate_limited(_admin_login_attempts, ip)
    if limited:
        return jsonify({"ok": False, "error": "Muitas tentativas. Tente novamente em alguns minutos."}), 429

    data = request.get_json(silent=True) or {}
    admin_password = data.get("adminPassword") or ""
    if not _admin_password_ok(admin_password):
        _record_failed_attempt(_admin_login_attempts, ip, count, window_start)
        return jsonify({"ok": False, "error": "Senha de administrador incorreta."}), 403
    _admin_login_attempts.pop(ip, None)

    filial_id = data.get("filialId")
    signatures = data.get("signatures")
    if filial_id is None or not isinstance(signatures, list) or not signatures:
        return jsonify({"ok": False, "error": "filialId e signatures (lista) sao obrigatorios."}), 400

    with _dedup_lock:
        dedup = _load_dedup()
        filial_groups = dedup.setdefault(str(filial_id), {})
        confirmed = []
        for signature in signatures:
            try:
                member_ids = _signature_to_ids(signature)
            except ValueError:
                continue
            canonical_id = max(member_ids, key=lambda pid: _estoque_sistema(pid, filial_id))
            filial_groups[signature] = {
                "status": "approved",
                "memberProductIds": member_ids,
                "canonicalProductId": canonical_id,
                "decidedAt": datetime.now().isoformat(),
            }
            confirmed.append(signature)
        _save_dedup(dedup)
    return jsonify({"ok": True, "confirmed": confirmed})


@app.route("/api/admin/dedup/reject", methods=["POST"])
def dedup_reject():
    ip = request.remote_addr or "unknown"
    limited, count, window_start = _rate_limited(_admin_login_attempts, ip)
    if limited:
        return jsonify({"ok": False, "error": "Muitas tentativas. Tente novamente em alguns minutos."}), 429

    data = request.get_json(silent=True) or {}
    admin_password = data.get("adminPassword") or ""
    if not _admin_password_ok(admin_password):
        _record_failed_attempt(_admin_login_attempts, ip, count, window_start)
        return jsonify({"ok": False, "error": "Senha de administrador incorreta."}), 403
    _admin_login_attempts.pop(ip, None)

    filial_id = data.get("filialId")
    signature = data.get("signature")
    if filial_id is None or not signature:
        return jsonify({"ok": False, "error": "filialId e signature sao obrigatorios."}), 400
    try:
        member_ids = _signature_to_ids(signature)
    except ValueError:
        return jsonify({"ok": False, "error": "signature invalida."}), 400

    with _dedup_lock:
        dedup = _load_dedup()
        filial_groups = dedup.setdefault(str(filial_id), {})
        filial_groups[signature] = {
            "status": "rejected",
            "memberProductIds": member_ids,
            "decidedAt": datetime.now().isoformat(),
        }
        _save_dedup(dedup)
    return jsonify({"ok": True})


@app.route("/api/dedup/groups")
def dedup_groups():
    filial_id = request.args.get("filialId", type=int)
    if filial_id is None:
        return jsonify({"ok": False, "error": "filialId e obrigatorio."}), 400
    dedup = _load_dedup()
    filial_groups = dedup.get(str(filial_id), {})
    produtos_by_id = {p.get("id"): p for p in CACHE.get("produtos", [])}
    approved = []
    for signature, group in filial_groups.items():
        if group.get("status") != "approved":
            continue
        members = []
        for pid in group.get("memberProductIds", []):
            p = produtos_by_id.get(pid)
            if p:
                members.append({
                    "id": pid, "descricao": p.get("descricao"), "ean": p.get("ean"),
                    "codproduto": p.get("codproduto"),
                })
        approved.append({
            "signature": signature,
            "canonicalProductId": group.get("canonicalProductId"),
            "members": members,
        })
    return jsonify({"ok": True, "groups": approved})
```

- [ ] **Step 4: Rodar os testes de novo, confirmar que passam**

Run: `python -m pytest tests/test_local_persistence.py -k "dedup_analyze or dedup_confirm or dedup_bulk_confirm or dedup_reject or dedup_groups or dedup_decisoes" -v`
Expected: PASS (9 testes).

- [ ] **Step 5: Rodar a suite inteira**

Run: `python -m pytest tests/ -q`
Expected: todos passando (79 no total até aqui).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_local_persistence.py
git commit -m "feat: endpoints de admin para analisar, confirmar, aprovar em lote e rejeitar duplicatas"
```

---

### Task 4: Interface — view "Deduplicação"

**Files:**
- Modify: `templates/index.html` (HTML da view + JS de interação)

**Interfaces:**
- Consome: endpoints da Task 3 (`/api/admin/dedup/analyze`, `/confirm`, `/bulk-confirm`, `/reject`, `GET /api/dedup/groups`); `S.isAdmin`, `S.adminPassword`, `S.filiais` (já existentes); `$`, `$$`, `fmt`, `escapeHtml`, `toast`, `apiGet` (já existentes); `updateNavForRole()`, `switchView()`, `popAdminFilialSelect()` (já existentes, serão modificadas).
- Não produz interface nova para outra task — é a ponta final deste plano (a Contagem Oficial, spec futuro, é quem vai consumir `GET /api/dedup/groups` depois).

Sem harness de teste JS neste projeto (confirmado nas features anteriores) — a verificação desta task é sintática (`node --check`) e a funcional acontece na Task 5 (Playwright).

- [ ] **Step 1: Adicionar o botão de navegação**

Em `templates/index.html`, localize a linha (por volta de 167 hoje):

```html
      <button data-view="bipadores" style="display:none">Bipadores</button>
```

Adicione logo depois:

```html
      <button data-view="dedup" style="display:none">Deduplicação</button>
```

- [ ] **Step 2: Adicionar a view HTML**

Localize o fechamento do `#bipadores-view` (procure por `<div id="bipadores-list"></div>` seguido de `</div>` e depois `</main>`, por volta da linha 283-285 hoje). Adicione, logo depois do `</div>` que fecha `#bipadores-view` e antes de `</main>`:

```html
    <div id="dedup-view" style="display:none">
      <div class="card" style="margin-bottom:16px">
        <h3 style="margin-bottom:12px">Deduplicação de Catálogo</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <select id="dedup-filial" style="flex:1;min-width:200px"><option value="">Selecione a loja</option></select>
          <button class="btn-p" id="btn-dedup-analyze">Analisar Duplicatas</button>
          <button class="btn-s btn-sm" id="btn-dedup-export">Exportar CSV</button>
        </div>
        <div id="dedup-status" style="font-size:.85rem;color:var(--muted);margin-top:8px"></div>
      </div>
      <div id="dedup-alta-section" class="card" style="margin-bottom:16px;display:none">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <h4>Confiança Alta (<span id="dedup-alta-count">0</span>)</h4>
          <button class="btn-s btn-sm" id="btn-dedup-bulk-approve">Aprovar Todos os Óbvios</button>
        </div>
        <div id="dedup-alta-list"></div>
      </div>
      <div id="dedup-review-section" class="card" style="margin-bottom:16px;display:none">
        <h4 style="margin-bottom:8px">Revisão Individual</h4>
        <div id="dedup-review-list"></div>
      </div>
      <div class="card">
        <h4 style="margin-bottom:8px">Grupos Confirmados</h4>
        <div id="dedup-confirmed-list" class="empty">Selecione uma loja para ver os grupos já confirmados.</div>
      </div>
    </div>
```

- [ ] **Step 3: Registrar a view em `switchView`, `updateNavForRole`**

Em `switchView` (procure por `['loading-view', 'dashboard-view', 'audit-view', 'reports-view', 'debug-view', 'bipadores-view'].forEach`), troque por:

```javascript
  ['loading-view', 'dashboard-view', 'audit-view', 'reports-view', 'debug-view', 'bipadores-view', 'dedup-view'].forEach(id => $(`#${id}`).style.display = 'none');
```

Na mesma função, logo depois de `else if (view === 'bipadores') { $('#bipadores-view').style.display = 'block'; popAdminFilialSelect(); loadBipadoresList(); }`, adicione:

```javascript
  else if (view === 'dedup') { $('#dedup-view').style.display = 'block'; popDedupFilialSelect(); }
```

Em `updateNavForRole` (procure por `const adminOnly = ['bipadores'];`), troque por:

```javascript
  const adminOnly = ['bipadores', 'dedup'];
```

- [ ] **Step 4: Generalizar `popAdminFilialSelect` para aceitar um seletor**

Localize:

```javascript
function popAdminFilialSelect() {
  const sel = $('#bip-filial');
  if (!sel) return;
  sel.innerHTML = '<option value="">Selecione a loja</option>' +
    S.filiais.map(f => `<option value="${f.id}">${f.fantasia || f.razaosocial} (${fmt(f.codigo)})</option>`).join('');
}
```

Troque por (mantém o comportamento padrão exatamente igual, só aceita um seletor opcional):

```javascript
function popAdminFilialSelect(selector = '#bip-filial') {
  const sel = $(selector);
  if (!sel) return;
  sel.innerHTML = '<option value="">Selecione a loja</option>' +
    S.filiais.map(f => `<option value="${f.id}">${f.fantasia || f.razaosocial} (${fmt(f.codigo)})</option>`).join('');
}

function popDedupFilialSelect() {
  popAdminFilialSelect('#dedup-filial');
}
```

- [ ] **Step 5: Adicionar as funções de interação**

Localize `function popDedupFilialSelect()` (que você acabou de adicionar no Step 4) e adicione logo depois:

```javascript
let dedupCandidates = [];
let dedupConfirmedGroups = [];

async function handleAnalyzeDuplicates() {
  const filialId = Number($('#dedup-filial').value);
  if (!filialId) { toast('Selecione uma loja primeiro.', 'warn'); return; }
  $('#dedup-status').textContent = 'Analisando...';
  try {
    const resp = await fetch('/api/admin/dedup/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adminPassword: S.adminPassword, filialId }),
    });
    const r = await resp.json();
    if (!r.ok) { toast('Erro: ' + r.error, 'err'); $('#dedup-status').textContent = ''; return; }
    dedupCandidates = r.candidates || [];
    renderDedupCandidates();
    $('#dedup-status').textContent = dedupCandidates.length
      ? `${dedupCandidates.length} grupo(s) candidato(s) encontrado(s).`
      : 'Nenhuma duplicata nova encontrada.';
    loadConfirmedDedupGroups(filialId);
  } catch (e) {
    toast('Falha ao analisar — verifique sua conexão.', 'err');
    $('#dedup-status').textContent = '';
  }
}

function renderDedupCandidates() {
  const alta = dedupCandidates.filter(c => c.confidence === 'alta');
  const outros = dedupCandidates.filter(c => c.confidence !== 'alta');

  $('#dedup-alta-section').style.display = alta.length ? 'block' : 'none';
  $('#dedup-alta-count').textContent = alta.length;
  $('#dedup-alta-list').innerHTML = alta.map(c => `
    <div class="recent-row" style="cursor:default">
      <span>${c.members.map(m => escapeHtml(m.descricao)).join(' + ')}</span>
      <span style="color:var(--muted);font-size:.75rem">${escapeHtml(c.signals.join(', '))}</span>
    </div>
  `).join('');

  $('#dedup-review-section').style.display = outros.length ? 'block' : 'none';
  $('#dedup-review-list').innerHTML = outros.map(c => {
    const defaultCanonical = c.members.reduce((a, b) => (b.estoqueFilial > a.estoqueFilial ? b : a)).id;
    return `
    <div class="card" style="margin-bottom:12px;border:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span class="badge ${c.confidence === 'media' ? 'bg-pendente' : 'bg-nao-encontrado'}">${c.confidence}</span>
        <span style="color:var(--muted);font-size:.75rem">${escapeHtml(c.signals.join(', '))}</span>
      </div>
      ${c.members.map(m => `
        <label style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
          <input type="radio" name="canonical-${c.signature}" value="${m.id}" ${m.id === defaultCanonical ? 'checked' : ''}>
          <span style="flex:1">${escapeHtml(m.descricao)} <span style="color:var(--muted);font-size:.75rem">(SKU ${escapeHtml(m.codproduto || '-')}, EAN ${escapeHtml(m.ean || '-')}, estoque ${m.estoqueFilial})</span></span>
        </label>
      `).join('')}
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn-s btn-sm" data-dedup-confirm="${c.signature}">Aprovar</button>
        <button class="btn-o btn-sm" data-dedup-reject="${c.signature}">Rejeitar</button>
      </div>
    </div>
  `;
  }).join('');
}

async function handleBulkApproveDedup() {
  const filialId = Number($('#dedup-filial').value);
  const signatures = dedupCandidates.filter(c => c.confidence === 'alta').map(c => c.signature);
  if (!signatures.length) return;
  try {
    const resp = await fetch('/api/admin/dedup/bulk-confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adminPassword: S.adminPassword, filialId, signatures }),
    });
    const r = await resp.json();
    if (!r.ok) { toast('Erro: ' + r.error, 'err'); return; }
    dedupCandidates = dedupCandidates.filter(c => !signatures.includes(c.signature));
    renderDedupCandidates();
    toast(`${r.confirmed.length} grupo(s) aprovado(s).`, 'ok');
    loadConfirmedDedupGroups(filialId);
  } catch (e) {
    toast('Falha ao aprovar em lote — verifique sua conexão.', 'err');
  }
}

async function handleConfirmDedupGroup(signature) {
  const filialId = Number($('#dedup-filial').value);
  const radio = document.querySelector(`input[name="canonical-${signature}"]:checked`);
  const canonicalProductId = radio ? Number(radio.value) : null;
  if (!canonicalProductId) { toast('Selecione o produto canônico.', 'warn'); return; }
  try {
    const resp = await fetch('/api/admin/dedup/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adminPassword: S.adminPassword, filialId, signature, canonicalProductId }),
    });
    const r = await resp.json();
    if (!r.ok) { toast('Erro: ' + r.error, 'err'); return; }
    dedupCandidates = dedupCandidates.filter(c => c.signature !== signature);
    renderDedupCandidates();
    toast('Grupo aprovado.', 'ok');
    loadConfirmedDedupGroups(filialId);
  } catch (e) {
    toast('Falha ao aprovar — verifique sua conexão.', 'err');
  }
}

async function handleRejectDedupGroup(signature) {
  const filialId = Number($('#dedup-filial').value);
  try {
    const resp = await fetch('/api/admin/dedup/reject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adminPassword: S.adminPassword, filialId, signature }),
    });
    const r = await resp.json();
    if (!r.ok) { toast('Erro: ' + r.error, 'err'); return; }
    dedupCandidates = dedupCandidates.filter(c => c.signature !== signature);
    renderDedupCandidates();
    toast('Grupo rejeitado.', 'ok');
  } catch (e) {
    toast('Falha ao rejeitar — verifique sua conexão.', 'err');
  }
}

async function loadConfirmedDedupGroups(filialId) {
  try {
    const r = await apiGet('dedup/groups', { filialId });
    dedupConfirmedGroups = r.groups || [];
    $('#dedup-confirmed-list').className = dedupConfirmedGroups.length ? '' : 'empty';
    $('#dedup-confirmed-list').innerHTML = dedupConfirmedGroups.length
      ? dedupConfirmedGroups.map(g => `
        <div class="recent-row" style="cursor:default">
          <span>${g.members.map(m => escapeHtml(m.descricao)).join(' + ')}</span>
          <span style="color:var(--muted);font-size:.75rem">canônico: ${escapeHtml(String(g.canonicalProductId))}</span>
        </div>
      `).join('')
      : 'Nenhum grupo confirmado ainda nesta loja.';
  } catch (e) {
    dedupConfirmedGroups = [];
  }
}

function exportDedupCSV() {
  const filialId = Number($('#dedup-filial').value);
  if (!filialId || !dedupConfirmedGroups.length) { toast('Nenhum grupo confirmado para exportar.', 'warn'); return; }
  const filial = S.filiais.find(f => f.id === filialId);
  const headers = ['Grupo', 'ID Produto', 'SKU', 'Descrição', 'EAN', 'Canônico'];
  const rows = [];
  dedupConfirmedGroups.forEach((g, i) => {
    g.members.forEach(m => {
      rows.push([i + 1, m.id, fmt(m.codproduto), fmt(m.descricao).replace(/,/g, ' '), fmt(m.ean), m.id === g.canonicalProductId ? 'Sim' : 'Não']);
    });
  });
  const csv = [headers, ...rows].map(r => r.map(v => '"' + (v ?? '') + '"').join(',')).join('\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'dedup_' + (filial ? (filial.fantasia || filial.razaosocial).replace(/\s/g, '_') : 'loja') + '_' + new Date().toISOString().slice(0, 10) + '.csv';
  a.click();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 6: Registrar os event listeners**

Localize, dentro de `async function init()`, a linha `$('#btn-export-csv').addEventListener('click', exportCSV);` e adicione logo depois:

```javascript
  $('#btn-dedup-analyze').addEventListener('click', handleAnalyzeDuplicates);
  $('#btn-dedup-bulk-approve').addEventListener('click', handleBulkApproveDedup);
  $('#btn-dedup-export').addEventListener('click', exportDedupCSV);
  $('#dedup-review-list').addEventListener('click', (e) => {
    const confirmBtn = e.target.closest('[data-dedup-confirm]');
    if (confirmBtn) { handleConfirmDedupGroup(confirmBtn.dataset.dedupConfirm); return; }
    const rejectBtn = e.target.closest('[data-dedup-reject]');
    if (rejectBtn) { handleRejectDedupGroup(rejectBtn.dataset.dedupReject); return; }
  });
```

- [ ] **Step 7: Verificar a sintaxe do JavaScript inline**

Extraia o conteúdo entre `<script>` e `</script>` de `templates/index.html` para um arquivo `.js` temporário (no diretório de scratchpad da sessão) e rode `node --check` nele.

Run: `node --check <caminho do .js extraído>`
Expected: sem erro de sintaxe.

- [ ] **Step 8: Commit**

```bash
git add templates/index.html
git commit -m "feat: interface de deduplicacao de catalogo (analise, revisao, aprovacao em lote, export)"
```

---

### Task 5: Verificação manual no navegador

Sem harness de teste JS neste projeto — validação funcional via Playwright, seguindo o mesmo padrão usado nas features anteriores desta sessão (Contagem de Estoque, Bipadores por Loja + Admin).

- [ ] **Step 1: Reiniciar o servidor local** (`python server.py`, matar processo antigo antes se já estiver rodando) e confirmar que sobe sem erro (a nova dependência `rapidfuzz` precisa estar instalada no ambiente que roda o servidor).

- [ ] **Step 2: Login como admin, abrir a aba "Deduplicação"** — confirmar que o botão aparece só para admin (some para bipador comum, igual "Bipadores" já se comporta).

- [ ] **Step 3: Selecionar uma loja real e clicar "Analisar Duplicatas"** — confirmar que a chamada completa sem erro no console, e que o texto de status reflete o resultado (seja "N grupo(s) encontrado(s)" ou "Nenhuma duplicata nova encontrada" — qualquer um dos dois é um resultado válido, já que não há garantia de que o catálogo real tenha duplicatas óbvias).

- [ ] **Step 4: Se houver candidatos de confiança alta, testar "Aprovar Todos os Óbvios"** — confirmar que os grupos saem da lista de candidatos e aparecem em "Grupos Confirmados". Se não houver nenhum candidato real disponível, criar um cenário de teste temporário: usar `POST /api/admin/dedup/confirm` diretamente (via `fetch` no console do navegador, ou revisando a lógica já coberta pelos testes automatizados da Task 3) só para confirmar que a lista "Grupos Confirmados" resultante renderiza corretamente — a cobertura de corretude do algoritmo em si já está garantida pelos testes pytest da Task 2.

- [ ] **Step 5: Se houver candidato de confiança média/baixa, testar o card de revisão individual** — trocar o produto canônico selecionado, aprovar um grupo e rejeitar outro, confirmar que cada um sai da fila e o aprovado aparece em "Grupos Confirmados".

- [ ] **Step 6: Testar isolamento por loja** — trocar para uma segunda loja no seletor, confirmar que "Grupos Confirmados" mostra uma lista diferente (vazia ou com outros grupos, nunca os mesmos da primeira loja).

- [ ] **Step 7: Testar "Exportar CSV"** — com pelo menos um grupo confirmado na loja selecionada, clicar e confirmar que baixa um arquivo `.csv` com o conteúdo esperado (grupo, produtos membros, qual é o canônico).

- [ ] **Step 8: Confirmar que nada quebrou no resto do app** — logar como bipador comum, confirmar que o fluxo de bipagem/contagem já existente continua funcionando normalmente (nenhuma regressão).

Se todos os passos passarem, nenhuma ação adicional é necessária além de reportar o resultado da verificação. Se algum passo falhar, trate como um achado normal do processo de subagent-driven-development (fix loop antes de prosseguir).
