"""
Persistencia de precios y consultas (F09, F10) — consigna 4 / criterio de aceptación 9.

Se usa SQLite (data/precios.db) en lugar de la base vectorial para los precios porque son
datos ESTRUCTURADOS que hay que filtrar, agregar y auditar (producto, competidor, fecha,
URL). Un vector store sirve para recuperar texto por similitud, no para responder
"¿cuál fue el precio regular de X en Carrefour el día Y?".
Cada fila guarda la fuente (URL) y la fecha de consulta original, de modo que todo precio
mostrado se puede verificar.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("PRECIOS_DB", os.path.join(RAIZ, "data", "precios.db"))
MAX_ANTIGUEDAD_HORAS = float(os.environ.get("MAX_ANTIGUEDAD_HORAS", "24"))

COLUMNAS = [
    "consulta_id", "fecha_consulta", "reutilizado", "producto_id", "producto", "marca", "categoria",
    "presentacion", "precio_ragnar", "moneda", "pais", "competidor", "estado", "nombre_encontrado",
    "marca_encontrada", "precio_regular", "precio_promocional", "promocion",
    "promociones_condicionadas", "disponible", "url", "fuente", "url_consulta", "confianza",
    "comparable", "motivos", "base_comparacion", "diferencia_absoluta_regular",
    "diferencia_porcentual_regular", "diferencia_absoluta_promo", "diferencia_porcentual_promo",
    "clasificacion", "clasificacion_promo", "precio_normalizado_ragnar",
    "precio_normalizado_competidor", "unidad_normalizacion", "detalle",
]


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cols = ", ".join(f"{c} TEXT" for c in COLUMNAS)
    con.execute(f"CREATE TABLE IF NOT EXISTS observaciones (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols})")
    con.execute("""CREATE TABLE IF NOT EXISTS consultas (
        consulta_id TEXT PRIMARY KEY, fecha TEXT, solicitud TEXT, producto_ids TEXT, competidores TEXT)""")
    return con


def _a_texto(v):
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


NUMERICAS = {"precio_ragnar", "precio_regular", "precio_promocional", "diferencia_absoluta_regular",
             "diferencia_porcentual_regular", "diferencia_absoluta_promo", "diferencia_porcentual_promo",
             "precio_normalizado_ragnar", "precio_normalizado_competidor"}


def _de_fila(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in NUMERICAS:
        if d.get(k) not in (None, ""):
            d[k] = float(d[k])
    for k in ("motivos", "promociones_condicionadas"):
        if d.get(k) and d[k].startswith("["):
            d[k] = json.loads(d[k])
    return d


def nueva_consulta(solicitud: str, producto_ids: list[str], competidores: list[str]) -> str:
    consulta_id = f"C-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    with _conn() as con:
        con.execute("INSERT INTO consultas VALUES (?,?,?,?,?)",
                    (consulta_id, datetime.now().astimezone().isoformat(timespec="seconds"),
                     solicitud, ",".join(producto_ids), ",".join(competidores)))
    return consulta_id


def guardar_observacion(obs: dict) -> int:
    fila = {c: _a_texto(obs.get(c)) for c in COLUMNAS}
    with _conn() as con:
        cur = con.execute(f"INSERT INTO observaciones ({', '.join(COLUMNAS)}) VALUES "
                          f"({', '.join('?' for _ in COLUMNAS)})", [fila[c] for c in COLUMNAS])
        return cur.lastrowid


def observacion_reciente(producto_id: str, competidor: str, max_horas: float = MAX_ANTIGUEDAD_HORAS) -> dict | None:
    """Última observación válida (no error de fuente) de un producto en un competidor."""
    limite = (datetime.now().astimezone() - timedelta(hours=max_horas)).isoformat(timespec="seconds")
    with _conn() as con:
        row = con.execute(
            """SELECT * FROM observaciones WHERE producto_id=? AND competidor=? AND fecha_consulta>=?
               AND estado NOT IN ('error_fuente','acceso_no_permitido') ORDER BY id DESC LIMIT 1""",
            (producto_id, competidor, limite)).fetchone()
    return _de_fila(row) if row else None


def obtener_observaciones(consulta_id: str = "", producto_id: str = "", competidor: str = "",
                          clasificacion: str = "", limite: int = 500) -> list[dict]:
    where, params = [], []
    if consulta_id:
        where.append("consulta_id=?"); params.append(consulta_id)
    if producto_id:
        ids = [p.strip().upper() for p in producto_id.split(",") if p.strip()]
        where.append(f"producto_id IN ({','.join('?' for _ in ids)})"); params += ids
    if competidor:
        where.append("lower(competidor) LIKE ?"); params.append(f"%{competidor.lower()}%")
    if clasificacion:
        where.append("(clasificacion LIKE ? OR estado LIKE ?)"); params += [f"{clasificacion}%"] * 2
    sql = "SELECT * FROM observaciones" + (f" WHERE {' AND '.join(where)}" if where else "")
    sql += " ORDER BY id DESC LIMIT ?"
    with _conn() as con:
        rows = con.execute(sql, params + [limite]).fetchall()
    return [_de_fila(r) for r in reversed(rows)]


def ultima_consulta_id() -> str | None:
    with _conn() as con:
        row = con.execute("SELECT consulta_id FROM consultas ORDER BY fecha DESC LIMIT 1").fetchone()
    return row["consulta_id"] if row else None


# --- Tools expuestas al agente ----------------------------------------------------------

_CAMPOS_RESUMEN = ["consulta_id", "fecha_consulta", "reutilizado", "producto_id", "producto", "marca",
                   "presentacion", "precio_ragnar", "competidor", "estado", "nombre_encontrado",
                   "precio_regular", "precio_promocional", "promocion", "confianza",
                   "base_comparacion", "diferencia_absoluta_regular", "diferencia_porcentual_regular",
                   "diferencia_porcentual_promo", "clasificacion", "url"]


def consultar_historial(consulta_id: str = "", producto_id: str = "", competidor: str = "",
                        clasificacion: str = "", limite: int = 50) -> dict:
    """
    Recupera precios ya investigados (guardados con fuente y fecha) para continuar un análisis
    anterior sin volver a buscar. Todos los filtros son opcionales y combinables.
    Args:
        consulta_id: str -> Id de una investigación previa (ej. "C-20261115-193012-ab12").
        producto_id: str -> Uno o varios ids separados por coma (ej. "RAG-00001,RAG-00002").
        competidor: str -> Nombre del competidor (ej. "Carrefour").
        clasificacion: str -> ragnar_mas_caro | ragnar_mas_barato | precio_similar |
                              sin_coincidencia_confiable | no_encontrado | error_fuente.
        limite: int -> Máximo de filas.
    Returns:
        Diccionario con las observaciones encontradas.
    """
    try:
        filas = obtener_observaciones(consulta_id, producto_id, competidor, clasificacion, limite)
        return {"total": len(filas),
                "observaciones": [{k: f.get(k) for k in _CAMPOS_RESUMEN if f.get(k) not in (None, "")}
                                  for f in filas]}
    except Exception as e:
        return {"error": str(e)}


def listar_consultas(limite: int = 10) -> dict:
    """
    Lista las últimas investigaciones realizadas (id, fecha, solicitud, productos y competidores).
    Args:
        limite: int -> Cantidad de consultas a mostrar.
    Returns:
        Diccionario con las consultas, de la más reciente a la más antigua.
    """
    try:
        with _conn() as con:
            rows = con.execute("SELECT * FROM consultas ORDER BY fecha DESC LIMIT ?", (limite,)).fetchall()
        return {"consultas": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": str(e)}
