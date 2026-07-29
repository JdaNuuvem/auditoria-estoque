"""Testes de conexao e extracao de dados da API i9logic.

Faz chamadas reais (nao mockadas) contra api.i9logic.net usando as mesmas
credenciais e rate limit do server.py, para validar que a integracao
continua funcionando e documentar o schema real de cada entidade.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time

import requests
import pytest

from server import API_BASE, HEADERS, _rate_limit

KNOWN_ENTITIES = ["filiais", "produtos", "produtos_estoques", "precos", "pedidos_produtos", "clientes", "usuarios"]


def _get(entity, **params):
    """GET com retry em 429 — a API real tambem tem rate limit no servidor dela,
    que pode estourar se o servidor Flask local estiver rodando (e sincronizando cache) ao mesmo tempo."""
    for attempt in range(3):
        _rate_limit()
        resp = requests.get(f"{API_BASE}/{entity}", headers=HEADERS, params=params, timeout=30)
        if resp.status_code != 429:
            return resp
        time.sleep(3 * (attempt + 1))
    return resp


@pytest.mark.parametrize("entity", KNOWN_ENTITIES)
def test_entity_connection_ok(entity):
    resp = _get(entity, page=1, per_page=1)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert isinstance(body.get("total"), int)
    assert body["total"] > 0


def test_produtos_schema_tem_campos_esperados():
    resp = _get("produtos", page=1, per_page=1)
    produto = resp.json()["data"][0]
    esperados = {"id", "descricao", "ean", "codproduto", "ativo", "marca", "fornecedor", "categoria", "ncm"}
    assert esperados.issubset(produto.keys())


def test_precos_schema_real_e_tabela_valor_nao_precovenda():
    """A API nao retorna precovenda/precocusto diretamente: retorna tabela+valor por produto.
    O front-end atual (S.pricesMap) le p.precovenda/p.precocusto, que nao existem no payload real."""
    resp = _get("precos", page=1, per_page=5)
    preco = resp.json()["data"][0]
    assert set(preco.keys()) == {"produto", "tabela", "valor"}
    assert "precovenda" not in preco
    assert "precocusto" not in preco


def test_pull_200_produtos():
    resp = _get("produtos", page=1, per_page=200)
    body = resp.json()
    assert body["ok"] is True
    assert len(body["data"]) == 200
    assert body["total"] >= 200
    ids = [p["id"] for p in body["data"]]
    assert len(set(ids)) == 200  # sem duplicados


def test_pedidos_exige_filtro_obrigatorio():
    """/pedidos sem filtro retorna 400 — comportamento real da API, nao um erro de conexao."""
    resp = _get("pedidos", page=1, per_page=1)
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "REQUIRED_FILTER_MISSING"


@pytest.mark.parametrize("entity", ["marcas", "fornecedores", "categorias", "fabricantes"])
def test_entidades_de_lookup_nao_existem(entity):
    """marca/fornecedor/categoria/fabricante em produtos sao so IDs crus — sem endpoint de lookup na API."""
    resp = _get(entity, page=1, per_page=1)
    assert resp.status_code == 404
