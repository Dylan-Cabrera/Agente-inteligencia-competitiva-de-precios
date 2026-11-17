"""
Investigación de precios en supermercados competidores (F03, F06, F09).
Consigna 4 (F03), consigna 6 (acceso respetuoso a fuentes) / criterios 3, 7 y 9.

Fuente principal: API pública de catálogo VTEX (`/api/catalog_system/pub/products/search`),
que usan Carrefour, Jumbo, Disco, Vea, Día y Más Online en Argentina. Se eligió porque:
- devuelve datos ESTRUCTURADOS (nombre, marca, precio de lista, precio con descuento,
  promociones, URL del producto) -> no hay que "leer" precios de HTML con el LLM;
- separa precio regular (ListPrice) de precio con descuento (Price) -> F06;
- la ruta /api/ no está prohibida en el robots.txt de esos sitios (se verifica en cada
  ejecución) y es la misma que usa el sitio público.

Acceso respetuoso: verificación de robots.txt, User-Agent identificable, 1 request por
segundo por dominio como máximo, timeout y reintentos con backoff ante 429/5xx.
Nunca se devuelven precios inventados: ante error o sin resultados se informa como tal.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime

USER_AGENT = os.environ.get(
    "AGENTE_USER_AGENT",
    "RagnarPriceResearchBot/1.0 (agente academico de comparacion de precios; consultas de bajo volumen)",
)
INTERVALO_MIN_SEG = float(os.environ.get("INTERVALO_MIN_SEG", "1.0"))
TIMEOUT_SEG = float(os.environ.get("TIMEOUT_SEG", "15"))

# Registro de competidores: vive en competidores.json (no en el código). El agente lo amplía con
# descubrir_competidor (tools/descubrimiento.py), que guarda URL + método (vtex | scraping) o si el
# sitio bloquea, para que la próxima consulta no tenga que volver a descubrirlo.
REGISTRO_JSON = os.environ.get("COMPETIDORES_JSON",
                               os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                            "competidores.json"))
COMPETIDORES: dict[str, dict] = {}
COMPETIDORES_POR_DEFECTO: list[str] = ["carrefour", "jumbo", "dia"]


def cargar_registro() -> dict[str, dict]:
    """(Re)carga competidores.json en COMPETIDORES (se modifica en el lugar: los imports lo ven)."""
    global COMPETIDORES_POR_DEFECTO
    try:
        with open(REGISTRO_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"competidores": {}}
    COMPETIDORES.clear()
    COMPETIDORES.update(data.get("competidores", {}))
    COMPETIDORES_POR_DEFECTO = data.get("por_defecto", COMPETIDORES_POR_DEFECTO)
    return COMPETIDORES


def guardar_competidor(clave: str, datos: dict) -> None:
    """Agrega o actualiza un competidor y persiste el registro (escritura atómica)."""
    cargar_registro()
    COMPETIDORES[clave] = datos
    tmp = REGISTRO_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"_doc": "Registro de supermercados competidores (lo amplía descubrir_competidor).",
                   "por_defecto": COMPETIDORES_POR_DEFECTO, "competidores": COMPETIDORES},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRO_JSON)


def activos() -> dict[str, dict]:
    """Competidores consultables (excluye los que bloquean el acceso automatizado)."""
    return {k: v for k, v in COMPETIDORES.items() if v.get("estado", "activo") == "activo"}


cargar_registro()

_ultimo_request: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def ahora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def resolver_competidores(texto: str | None, pais: str | None = None) -> tuple[list[str], list[str]]:
    """
    Convierte "Carrefour, Día" en claves del registro. Devuelve (claves_validas, desconocidos).
    Si el texto está vacío usa los competidores por defecto del país.
    """
    from tools.matching import normalizar

    t = (texto or "").strip().lower()
    claves, desconocidos = [], []
    if not t:
        claves = list(COMPETIDORES_POR_DEFECTO)
    elif t in {"todos", "all"}:
        claves = list(activos())
    else:
        for parte in t.replace(";", ",").replace(" y ", ",").split(","):
            p = normalizar(parte).replace(" ", "")
            if len(p) < 3:
                continue
            clave = next((k for k, v in COMPETIDORES.items()
                          if p == k or p in normalizar(v["nombre"]).replace(" ", "")), None)
            if clave:
                claves.append(clave)
            else:
                desconocidos.append(parte.strip())
        claves = list(dict.fromkeys(claves))
    if pais:
        claves = [c for c in claves if COMPETIDORES[c]["pais"] == pais]
    return claves, desconocidos


# --- HTTP respetuoso --------------------------------------------------------------------

def _permitido(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    origen = f"{parsed.scheme}://{parsed.netloc}"
    if origen not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(f"{origen}/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:
                rp.parse(r.read().decode("utf-8", "ignore").splitlines())
        except urllib.error.HTTPError as e:
            # 4xx sin robots.txt => sin restricciones; 5xx => no sabemos, no accedemos.
            rp = rp if 400 <= e.code < 500 else None
            if rp:
                rp.parse([])
        except Exception:
            rp = None
        _robots[origen] = rp
    rp = _robots[origen]
    return bool(rp and rp.can_fetch(USER_AGENT, url))


def _esperar_turno(dominio: str) -> None:
    espera = INTERVALO_MIN_SEG - (time.monotonic() - _ultimo_request.get(dominio, 0))
    if espera > 0:
        time.sleep(espera)
    _ultimo_request[dominio] = time.monotonic()


def http_get_json(url: str, reintentos: int = 2):
    if not _permitido(url):
        raise PermissionError(f"robots.txt no permite acceder a {url} (o no se pudo verificar)")
    dominio = urllib.parse.urlparse(url).netloc
    for intento in range(reintentos + 1):
        _esperar_turno(dominio)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and intento < reintentos:
                time.sleep(2 ** (intento + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if intento < reintentos:
                time.sleep(2 ** (intento + 1))
                continue
            raise


# --- VTEX -------------------------------------------------------------------------------

def _parsear_producto_vtex(p: dict, competidor: dict, fecha: str) -> dict | None:
    items = p.get("items") or []
    if not items or not items[0].get("sellers"):
        return None
    oferta = items[0]["sellers"][0].get("commertialOffer", {})
    price, list_price = oferta.get("Price") or 0, oferta.get("ListPrice") or 0
    sin_desc = oferta.get("PriceWithoutDiscount") or 0
    # Precio regular = PriceWithoutDiscount (precio sin descuentos aplicados). ListPrice NO es
    # confiable: en los sitios de Cencosud (Jumbo, Disco, Vea) viene ~80 veces inflado.
    regular = sin_desc or list_price or price
    if list_price and sin_desc and list_price > sin_desc * 3:
        regular = sin_desc
    if not regular:
        return None
    promo = price if price and price < regular - 0.009 else None
    if promo and promo < regular * 0.2:  # descuento > 80 %: dato inconsistente, no se informa
        promo = None
    descripcion_promo = None
    if promo:
        descripcion_promo = f"{round((1 - promo / regular) * 100)}% de descuento sobre precio regular"
        nombres = [d.get("Name") for d in oferta.get("DiscountHighLight", []) if d.get("Name")]
        if nombres:
            descripcion_promo += f" ({'; '.join(nombres)})"
    condicionadas = [t.get("Name") for t in oferta.get("PromotionTeasers", []) if t.get("Name")]
    link = p.get("link") or ""
    if link.startswith("/"):
        link = competidor["base"] + link
    return {
        "competidor": competidor["nombre"],
        "nombre_encontrado": p.get("productName", ""),
        "marca": p.get("brand", ""),
        "precio_regular": round(float(regular), 2),
        "precio_promocional": round(float(promo), 2) if promo else None,
        "promocion": descripcion_promo,
        "promociones_condicionadas": condicionadas or None,  # ej. "Tarjeta X 15%", "2do al 50%"
        "disponible": bool(oferta.get("IsAvailable")),
        "moneda": competidor["moneda"],
        "pais": competidor["pais"],
        "url": link,
        "fuente": f"API pública VTEX de {competidor['nombre']}",
        "fecha_consulta": fecha,
    }


def buscar_en_competidor(clave: str, consulta: str, max_resultados: int = 8) -> dict:
    """Busca una consulta de texto en un competidor. Nunca lanza: devuelve estado + resultados."""
    comp = COMPETIDORES[clave]
    if comp.get("estado") == "bloqueado":
        return {"competidor": comp["nombre"], "estado": "acceso_no_permitido",
                "detalle": f"{comp['nombre']} bloquea el acceso automatizado ({comp.get('motivo', '')}); "
                           "no se evade el bloqueo.",
                "resultados": [], "url_consulta": comp["base"], "fecha_consulta": ahora_iso()}
    if comp.get("tipo") == "scraping":
        from tools.scraper_web import buscar_scraping
        return buscar_scraping(clave, consulta, max_resultados)
    fecha = ahora_iso()
    url = (f"{comp['base']}/api/catalog_system/pub/products/search?"
           f"ft={urllib.parse.quote(consulta)}&_from=0&_to={max(0, max_resultados - 1)}")
    try:
        data = http_get_json(url)
    except PermissionError as e:
        return {"competidor": comp["nombre"], "estado": "acceso_no_permitido", "detalle": str(e),
                "resultados": [], "url_consulta": url, "fecha_consulta": fecha}
    except Exception as e:
        return {"competidor": comp["nombre"], "estado": "error_fuente", "detalle": f"{type(e).__name__}: {e}",
                "resultados": [], "url_consulta": url, "fecha_consulta": fecha}
    resultados = [r for r in (_parsear_producto_vtex(p, comp, fecha) for p in data or []) if r]
    return {"competidor": comp["nombre"], "estado": "ok" if resultados else "sin_resultados",
            "resultados": resultados, "url_consulta": url, "fecha_consulta": fecha}


def buscar_precio_competencia(consulta: str, competidores: str = "", max_resultados: int = 5) -> dict:
    """
    Busca un producto en los sitios de supermercados competidores y devuelve precios REALES
    con fuente (URL) y fecha de consulta. Diferencia precio regular, precio promocional y
    promociones condicionadas (tarjetas, 2x1, etc.).
    No evalúa si es el mismo producto: para eso usar investigar_productos.
    Args:
        consulta: str -> Texto a buscar, ej. "Yerba mate Playadito 500 g".
        competidores: str -> Lista separada por comas (ej. "Carrefour, Día"). Vacío = Carrefour, Jumbo y Día.
                             "todos" = todos los competidores registrados.
        max_resultados: int -> Resultados por competidor.
    Returns:
        Diccionario con los resultados por competidor (estado ok / sin_resultados / error_fuente).
    """
    claves, desconocidos = resolver_competidores(competidores)
    salida = {"consulta": consulta, "resultados_por_competidor": [
        buscar_en_competidor(c, consulta, max_resultados) for c in claves]}
    if desconocidos:
        salida["competidores_no_soportados"] = desconocidos
        salida["nota"] = ("Competidores sin fuente estructurada configurada. Disponibles: "
                          + ", ".join(v["nombre"] for v in COMPETIDORES.values()))
    return salida


def listar_competidores() -> list[dict]:
    """
    Lista los supermercados del registro (competidores.json): país, moneda, método (API VTEX o
    scraping) y si bloquean el acceso automatizado. Si el usuario nombra uno que no está, usar
    descubrir_competidor.
    Returns:
        Lista de competidores registrados.
    """
    cargar_registro()
    return [{"clave": k, "nombre": v["nombre"], "pais": v["pais"], "moneda": v["moneda"],
             "metodo": v.get("tipo"), "estado": v.get("estado", "activo"),
             **({"motivo": v["motivo"]} if v.get("motivo") else {}),
             "por_defecto": k in COMPETIDORES_POR_DEFECTO} for k, v in COMPETIDORES.items()]
