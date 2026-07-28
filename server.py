import json
import os
import time
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
    app.run(host="0.0.0.0", port=5000, debug=False)
