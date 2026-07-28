import json
import os
import time
import threading
from datetime import date, datetime
import requests
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_BASE = "https://api.i9logic.net/v1"
CLIENT_ID = "AA5F52211F2B19AE605A245C"
TOKEN = "c87407fcbe574263ce9477b2a61421a4837781cce3012d2c3bb4f3cf1070486f"
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")

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
CACHE = {"filiais": [], "produtos": [], "estoques": [], "precos": [], "ready": False, "loading": False}


def _paginated_fetch(entity, filter_fn=None, per_page=200):
    all_data = []
    page = 1
    while True:
        _rate_limit()
        resp = requests.get(f"{API_BASE}/{entity}", headers=HEADERS,
                          params={"page": page, "per_page": per_page}, timeout=45)
        body = resp.json()
        if not body.get("ok"):
            break
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
    CACHE["loading"] = True
    try:
        CACHE["filiais"] = _paginated_fetch("filiais", per_page=100)
        CACHE["produtos"] = _paginated_fetch("produtos", filter_fn=lambda p: p.get("ean") and str(p["ean"]).strip())
        CACHE["estoques"] = _paginated_fetch("produtos_estoques")
        CACHE["precos"] = _paginated_fetch("precos")
        CACHE["ready"] = True
    finally:
        CACHE["loading"] = False


@app.route("/api/cache/status")
def cache_status():
    return jsonify({"ok": True, "ready": CACHE["ready"], "loading": CACHE["loading"],
                    "counts": {"filiais": len(CACHE["filiais"]), "produtos": len(CACHE["produtos"]),
                               "estoques": len(CACHE["estoques"]), "precos": len(CACHE["precos"])}})


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


def _load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


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


@app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy(path):
    url = f"{API_BASE}/{path}" if path else f"{API_BASE}"
    params = request.args.to_dict()
    _rate_limit()
    try:
        method = request.method.lower()
        json_body = (request.get_json(silent=True) or {}) if method in ("post", "patch") else None
        resp = requests.request(method, url, headers=HEADERS, params=params, json=json_body, timeout=45)
        mime = resp.headers.get("Content-Type", "application/json")
        return resp.content, resp.status_code, {"Content-Type": mime, "Access-Control-Allow-Origin": "*"}
    except requests.exceptions.RequestException as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.route("/")
def index():
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    return send_file(os.path.join(template_dir, "index.html"))


if __name__ == "__main__":
    print("Servidor iniciado em http://localhost:5000")
    threading.Thread(target=refresh_cache, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
