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

load_dotenv()

app = Flask(__name__)
CORS(app)

API_BASE = "https://api.i9logic.net/v1"
CLIENT_ID = os.environ["I9LOGIC_CLIENT_ID"]
TOKEN = os.environ["I9LOGIC_TOKEN"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
if len(ADMIN_PASSWORD) < 8:
    raise RuntimeError("ADMIN_PASSWORD ausente ou muito curta (minimo 8 caracteres).")


def _admin_password_ok(candidate):
    return hmac.compare_digest(str(candidate or "").encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"))


_admin_login_attempts = {}
ADMIN_LOGIN_MAX_ATTEMPTS = 10
ADMIN_LOGIN_WINDOW_SECONDS = 300

DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
USERS_FILE = os.path.join(DATA_DIR, "users.json")
CACHE_FILE = os.path.join(DATA_DIR, "cache_data.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit_sessions.json")
_audit_lock = threading.Lock()

HEADERS = {
    "X-Client-Id": CLIENT_ID,
    "Authorization": f"Bearer {TOKEN}",
}

_last_request_time = 0
_MIN_INTERVAL = 2.1


def _rate_limit():
    global _last_request_time
    now = time.time()
    wait = _last_request_time + _MIN_INTERVAL - now
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


# ponytail: server-side cache, single source for all users
CACHE = {"filiais": [], "produtos": [], "estoques": [], "precos": [], "ready": False, "loading": False, "progress": {}}


def _paginated_fetch(entity, filter_fn=None, per_page=200):
    all_data = []
    page = 1
    while True:
        _rate_limit()
        resp = requests.get(f"{API_BASE}/{entity}", headers=HEADERS,
                          params={"page": page, "per_page": per_page}, timeout=45)
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"Falha ao buscar '{entity}' pagina {page}: {body.get('error') or resp.status_code}")
        data = body.get("data", [])
        if filter_fn:
            data = [d for d in data if filter_fn(d)]
        all_data.extend(data)
        total = body.get("total", 0)
        if len(all_data) >= total or len(data) == 0:
            break
        page += 1
    return all_data


def refresh_cache():
    """So confirma (ready + grava em disco) apos as 4 entidades sincronizarem por completo.
    Uma falha parcial (ex: rate limit) mantem o cache anterior intacto em vez de gravar dados incompletos.
    'progress' vai sendo preenchido entidade-a-entidade so para a barra de progresso do front."""
    CACHE["loading"] = True
    CACHE["progress"] = {}
    try:
        filiais = _paginated_fetch("filiais", per_page=100)
        CACHE["progress"]["filiais"] = len(filiais)
        produtos = _paginated_fetch("produtos", filter_fn=lambda p: p.get("ean") and str(p["ean"]).strip())
        CACHE["progress"]["produtos"] = len(produtos)
        estoques = _paginated_fetch("produtos_estoques")
        CACHE["progress"]["estoques"] = len(estoques)
        precos = _paginated_fetch("precos")
        CACHE["progress"]["precos"] = len(precos)

        CACHE["filiais"] = filiais
        CACHE["produtos"] = produtos
        CACHE["estoques"] = estoques
        CACHE["precos"] = precos
        CACHE["ready"] = True
        CACHE["synced_at"] = datetime.now().isoformat()
        _save_cache_to_disk()
    except Exception as exc:
        print(f"[cache] Falha ao sincronizar: {exc}. Cache anterior mantido.")
    finally:
        CACHE["loading"] = False
        CACHE["progress"] = {}


def _save_cache_to_disk():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "filiais": CACHE["filiais"], "produtos": CACHE["produtos"],
            "estoques": CACHE["estoques"], "precos": CACHE["precos"],
            "synced_at": CACHE.get("synced_at"),
        }, f, ensure_ascii=False)


def _load_cache_from_disk():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    CACHE["filiais"] = data.get("filiais", [])
    CACHE["produtos"] = data.get("produtos", [])
    CACHE["estoques"] = data.get("estoques", [])
    CACHE["precos"] = data.get("precos", [])
    CACHE["synced_at"] = data.get("synced_at")
    CACHE["ready"] = bool(CACHE["produtos"])
    return CACHE["ready"]


@app.route("/api/cache/status")
def cache_status():
    if CACHE["loading"] and CACHE.get("progress"):
        progress = CACHE["progress"]
        counts = {k: progress.get(k, 0) for k in ("filiais", "produtos", "estoques", "precos")}
    else:
        counts = {"filiais": len(CACHE["filiais"]), "produtos": len(CACHE["produtos"]),
                   "estoques": len(CACHE["estoques"]), "precos": len(CACHE["precos"])}
    return jsonify({"ok": True, "ready": CACHE["ready"], "loading": CACHE["loading"],
                    "synced_at": CACHE.get("synced_at"), "counts": counts})


@app.route("/api/cache/<entity>")
def cache_get(entity):
    if entity not in CACHE:
        return jsonify({"ok": False, "error": f"Entidade '{entity}' nao existe no cache"}), 404
    return jsonify({"ok": True, "data": CACHE[entity]})


@app.route("/api/cache/reload", methods=["POST"])
def cache_reload():
    if CACHE["loading"]:
        return jsonify({"ok": False, "error": "Cache ja esta sendo carregado"}), 409
    threading.Thread(target=refresh_cache, daemon=True).start()
    return jsonify({"ok": True, "message": "Recarregando cache em background"})


DEBUG_ENTITIES = ["filiais", "produtos", "produtos_estoques", "precos", "pedidos_produtos", "clientes", "usuarios"]


@app.route("/api/debug/test-connection")
def debug_test_connection():
    results = []
    for entity in DEBUG_ENTITIES:
        _rate_limit()
        start = time.time()
        try:
            resp = requests.get(f"{API_BASE}/{entity}", headers=HEADERS,
                              params={"page": 1, "per_page": 1}, timeout=20)
            elapsed = round(time.time() - start, 2)
            body = resp.json()
            data = body.get("data", [])
            sample = data[0] if data else {}
            results.append({
                "entity": entity, "status": resp.status_code, "ok": bool(body.get("ok")),
                "total": body.get("total"), "elapsed_s": elapsed,
                "campos": sorted(sample.keys()),
            })
        except Exception as exc:
            results.append({"entity": entity, "status": None, "ok": False, "error": str(exc)})
    return jsonify({"ok": True, "results": results})


@app.route("/api/debug/pull200")
def debug_pull200():
    """Amostra do CACHE ja sincronizado (uma unica vez, compartilhado por todos os usuarios).
    Nao faz nenhuma chamada nova a API — usa exatamente os mesmos dados que /api/cache/produtos serve."""
    start = time.time()
    per_page = request.args.get("per_page", default=200, type=int)

    if not CACHE.get("ready"):
        return jsonify({"ok": False, "error": "Cache ainda nao sincronizado. Aguarde a sincronizacao inicial."}), 409

    produtos = CACHE.get("produtos", [])[:per_page]
    ids = {p["id"] for p in produtos}

    estoque_by_produto = {}
    for e in CACHE.get("estoques", []):
        if e.get("idproduto") in ids:
            estoque_by_produto.setdefault(e["idproduto"], []).append(e)

    precos_by_produto = {}
    for pr in CACHE.get("precos", []):
        if pr.get("produto") in ids:
            precos_by_produto.setdefault(pr["produto"], []).append(pr)

    enriched = [{
        **p,
        "_estoques": estoque_by_produto.get(p["id"], []),
        "_estoque_total": sum(e.get("qtd") or 0 for e in estoque_by_produto.get(p["id"], [])),
        "_precos": precos_by_produto.get(p["id"], []),
    } for p in produtos]

    return jsonify({
        "ok": True,
        "total_no_cache": len(CACHE.get("produtos", [])),
        "quantidade_puxada": len(enriched),
        "tempo_segundos": round(time.time() - start, 2),
        "sincronizado_em": CACHE.get("synced_at"),
        "cache_estoques_disponivel": len(CACHE.get("estoques", [])) > 0,
        "cache_precos_disponivel": len(CACHE.get("precos", [])) > 0,
        "produtos": enriched,
    })


def _load_audit():
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_audit(sessions):
    with open(AUDIT_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False)


@app.route("/api/audit/sessions", methods=["GET"])
def audit_sessions():
    sessions = _load_audit()
    return jsonify({"ok": True, "sessions": list(sessions.values())})


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


@app.route("/api/audit/scan", methods=["POST"])
def audit_scan():
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId")
    product_id = data.get("productId")
    ean = data.get("ean") or ""
    descricao = data.get("descricao") or ""
    if not session_id or product_id is None:
        return jsonify({"ok": False, "error": "sessionId e productId sao obrigatorios."}), 400
    with _audit_lock:
        sessions = _load_audit()
        session = sessions.get(session_id)
        if not session:
            return jsonify({"ok": False, "error": "Sessao de auditoria nao encontrada."}), 404
        pid = str(product_id)
        is_dup = pid in session["encontrados"]
        if not is_dup:
            session["encontrados"][pid] = {
                "ean": ean, "descricao": descricao,
                "scannedAt": datetime.now().strftime("%H:%M:%S"),
            }
            _save_audit(sessions)
    return jsonify({"ok": True, "dup": is_dup, "entry": session["encontrados"][pid]})


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
    ean = (data.get("ean") or "")[:64]
    descricao = (data.get("descricao") or "")[:200]
    has_delta = "delta" in data
    has_qtd = "qtd" in data

    if not session_id or product_id is None:
        return jsonify({"ok": False, "error": "sessionId e productId sao obrigatorios."}), 400
    try:
        int(product_id)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "productId deve ser um numero."}), 400
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
        "qtdSistemaDisponivel": bool(CACHE.get("estoques")),
    })


def _load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    ip = request.remote_addr or "unknown"
    now = time.time()
    count, window_start = _admin_login_attempts.get(ip, (0, now))
    if now - window_start > ADMIN_LOGIN_WINDOW_SECONDS:
        count, window_start = 0, now
    if count >= ADMIN_LOGIN_MAX_ATTEMPTS:
        return jsonify({"ok": False, "error": "Muitas tentativas. Tente novamente em alguns minutos."}), 429

    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if not _admin_password_ok(password):
        _admin_login_attempts[ip] = (count + 1, window_start)
        return jsonify({"ok": False, "error": "Senha incorreta."}), 401
    _admin_login_attempts.pop(ip, None)
    return jsonify({"ok": True})


@app.route("/api/admin/bipadores", methods=["POST"])
def admin_create_bipador():
    data = request.get_json(silent=True) or {}
    admin_password = data.get("adminPassword") or ""
    if not _admin_password_ok(admin_password):
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


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    if not email or not name:
        return jsonify({"ok": False, "error": "Email e nome são obrigatórios."}), 400
    users = _load_users()
    user = users.get(email)
    if not user:
        return jsonify({"ok": False, "error": "Email não cadastrado."}), 404
    if user["name"].lower() != name.lower():
        return jsonify({"ok": False, "error": "Nome não confere."}), 401
    return jsonify({"ok": True, "user": user})


@app.route("/api/auth/users", methods=["GET"])
def list_users():
    users = _load_users()
    return jsonify({"ok": True, "users": list(users.values())})


@app.route("/api/sales/<int:idproduto>")
def product_sales(idproduto):
    stats = {"idproduto": idproduto, "total_sales": 0, "total_qty": 0, "total_value": 0,
             "last_sale_date": None, "days_without_sale": None, "first_sale_date": None,
             "sales_by_filial": {}}
    pedido_ids = []
    page = 1
    while True:
        _rate_limit()
        resp = requests.get(f"{API_BASE}/pedidos_produtos", headers=HEADERS,
                          params={"idproduto": idproduto, "page": page, "per_page": 200}, timeout=45)
        body = resp.json()
        if not body.get("ok"):
            break
        data = body.get("data", [])
        for item in data:
            qtd = item.get("qtd") or 0
            valor = item.get("valorvenda") or 0
            stats["total_qty"] += qtd
            stats["total_value"] += valor * qtd
            stats["total_sales"] += 1
            if len(pedido_ids) < 5:
                pedido_ids.append(item["idpedido"])
        total = body.get("total", 0)
        if len(stats) >= total or len(data) == 0:
            stats["total_sales"] = total
            break
        page += 1

    if pedido_ids:
        dates = []
        for pid in pedido_ids[:3]:
            _rate_limit()
            r = requests.get(f"{API_BASE}/pedidos/{pid}", headers=HEADERS, timeout=45)
            b = r.json()
            if b.get("ok"):
                d = b.get("data", {}).get("data")
                if d:
                    dates.append(d)
        if dates:
            dates.sort(reverse=True)
            stats["last_sale_date"] = dates[0]
            stats["first_sale_date"] = dates[-1]
            last_dt = datetime.strptime(dates[0], "%Y-%m-%d").date()
            stats["days_without_sale"] = (date.today() - last_dt).days

    return jsonify({"ok": True, "stats": stats})


@app.route("/")
def index():
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    return send_file(os.path.join(template_dir, "index.html"))


if __name__ == "__main__":
    print("Servidor iniciado em http://localhost:5000")
    if _load_cache_from_disk():
        print(f"Cache carregado do disco: {len(CACHE['produtos'])} produtos (sincronizado em {CACHE.get('synced_at')}). "
              "Use 'Recarregar Dados' para resincronizar com a API.")
    else:
        threading.Thread(target=refresh_cache, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
