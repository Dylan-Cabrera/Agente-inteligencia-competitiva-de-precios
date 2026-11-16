"""
Consulta del catálogo de Ragnar (F01, F02) — consigna 4 / criterios de aceptación 1 y 2.

Recuperación HÍBRIDA:
1. Búsqueda exacta por producto_id (ej. "RAG-00042"): los embeddings no son buenos con ids.
2. Filtros estructurados por categoría / marca / país (el catálogo es una tabla).
3. Búsqueda léxica (tokens normalizados, sin tildes) + búsqueda semántica (ChromaDB +
   nomic-embed-text), fusionadas con Reciprocal Rank Fusion (RRF). La semántica cubre
   sinónimos ("gaseosa" ~ "bebida cola"), la léxica cubre marcas y números ("900 g").
   Si Ollama no está disponible se usa solo la léxica (degradación controlada).

Es de SOLO LECTURA: ninguna función escribe sobre catalogo_ragnar.csv (restricción: no
modificar precios de Ragnar).
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache

from tools.matching import normalizar, tokens
from tools.tools import CATALOGO_CSV

_ID_RE = re.compile(r"\bRAG-?\d{1,5}\b", re.IGNORECASE)


@lru_cache(maxsize=1)
def _catalogo() -> tuple[dict, ...]:
    with open(CATALOGO_CSV, encoding="utf-8-sig", newline="") as f:
        return tuple(dict(r) for r in csv.DictReader(f))


def cargar_catalogo() -> list[dict]:
    return [dict(p) for p in _catalogo()]


def _normalizar_id(texto: str) -> str:
    num = re.sub(r"\D", "", texto)
    return f"RAG-{int(num):05d}"


def obtener_producto(producto_id: str) -> dict | None:
    pid = _normalizar_id(producto_id)
    return next((dict(p) for p in _catalogo() if p["producto_id"] == pid), None)


def _producto_publico(p: dict) -> dict:
    out = {k: p[k] for k in ("producto_id", "producto", "marca", "categoria", "presentacion",
                             "contenido", "unidad_medida", "precio", "moneda", "pais")}
    out["precio"] = float(out["precio"])
    return out


def _score_lexico(query_toks: list[str], nums: list[str], p: dict) -> float:
    if not query_toks:
        return 0.0
    texto = f"{p['producto']} {p['marca']} {p['categoria']} {p['presentacion']}"
    ptoks = set(tokens(texto))
    puntaje = sum(1 for t in query_toks if t in ptoks) / len(query_toks)
    # Coincidencia de cantidad (ej. "900" en la consulta y contenido 900)
    if p["contenido"] in nums:
        puntaje += 0.25
    return puntaje


def consultar_catalogo(query: str = "", categoria: str = "", marca: str = "", k: int = 10) -> dict:
    """
    Consulta SOLO LECTURA el catálogo de productos de Ragnar S.A. Usar SIEMPRE antes de buscar
    precios en la competencia. Combina búsqueda por id, filtros y búsqueda léxica + semántica.
    Args:
        query: str -> Producto, marca, presentación o producto_id (ej. "Yerba Playadito 500 g", "RAG-00042").
                      Puede ir vacío si se filtra por categoría o marca.
        categoria: str -> Filtro opcional: Alimentos, Bebidas, Higiene personal, Hogar o Limpieza.
        marca: str -> Filtro opcional por marca (ej. "Dove").
        k: int -> Máximo de productos a devolver.
    Returns:
        Diccionario con total_coincidencias, modo de búsqueda y lista de productos (producto_id,
        producto, marca, categoria, presentacion, contenido, unidad_medida, precio, moneda, pais).
    """
    try:
        catalogo = _catalogo()
        k = max(1, min(int(k or 10), 50))

        ids = _ID_RE.findall(query or "")
        if ids:
            encontrados = [obtener_producto(i) for i in ids]
            productos = [_producto_publico(p) for p in encontrados if p]
            no = [i for i, p in zip(ids, encontrados) if not p]
            out = {"modo": "id_exacto", "total_coincidencias": len(productos), "productos": productos}
            if no:
                out["no_encontrados"] = no
            return out

        candidatos = list(catalogo)
        if categoria:
            c = normalizar(categoria)
            candidatos = [p for p in candidatos if c in normalizar(p["categoria"])]
        if marca:
            m = normalizar(marca).replace(" ", "")
            candidatos = [p for p in candidatos if m in normalizar(p["marca"]).replace(" ", "")]

        modo, lex = "filtros", None
        if query and query.strip():
            q_toks = tokens(query)
            nums = re.findall(r"\d+", normalizar(query))
            lex = sorted(((p["producto_id"], _score_lexico(q_toks, nums, p)) for p in candidatos),
                         key=lambda x: -x[1])
            lex = [x for x in lex if x[1] > 0]
            ranking = {pid: 1 / (60 + i) for i, (pid, _) in enumerate(lex)}
            modo = "lexica"
            try:
                from tools.tools import busqueda_semantica_catalogo
                ids_validos = {p["producto_id"] for p in candidatos}
                sem = [pid for pid, s in busqueda_semantica_catalogo(query, k=40) if pid in ids_validos]
                for i, pid in enumerate(sem):
                    ranking[pid] = ranking.get(pid, 0) + 1 / (60 + i)
                modo = "hibrida (lexica + semantica, RRF)"
            except Exception as e:  # Ollama/Chroma no disponibles
                modo = f"lexica (semantica no disponible: {str(e)[:80]})"
            # Si hay coincidencias léxicas fuertes se priorizan (marca + producto exactos).
            fuertes = {pid: round(s, 2) for pid, s in lex if s >= 0.99}
            orden = sorted(ranking, key=lambda pid: (-fuertes.get(pid, 0), -ranking[pid]))
            por_id = {p["producto_id"]: p for p in candidatos}
            candidatos = [por_id[pid] for pid in orden]

        productos = [_producto_publico(p) for p in candidatos[:k]]
        out = {"modo": modo, "total_coincidencias": len(candidatos), "productos": productos}
        if not productos:
            out["mensaje"] = "No hay productos en el catálogo de Ragnar que coincidan con la solicitud."
        elif lex is not None and not lex:
            out["mensaje"] = ("Ningún producto contiene literalmente los términos buscados: los resultados son "
                              "aproximaciones semánticas. Verificar que sean lo pedido (o confirmar con el "
                              "usuario) antes de investigar; puede que Ragnar no comercialice ese producto.")
        elif len(candidatos) > k:
            out["mensaje"] = f"Se muestran {k} de {len(candidatos)}. Refinar la búsqueda o aumentar k."
        return out
    except Exception as e:
        return {"error": str(e)}
