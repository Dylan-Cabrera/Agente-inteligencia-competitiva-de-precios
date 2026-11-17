"""
Descubrimiento de supermercados nuevos (F03) y tipo de cambio (restricción: diferencias monetarias).

descubrir_competidor: cuando el usuario nombra un supermercado que no está en competidores.json,
  1. busca su sitio oficial en la web (web_search),
  2. verifica robots.txt,
  3. prueba el método de acceso, del más estructurado al más frágil:
       a) API pública VTEX (/api/catalog_system/pub/products/search)
       b) scraping de la página de búsqueda (JSON-LD o __NEXT_DATA__, ver scraper_web.py)
  4. guarda el resultado en competidores.json: URL + método, o "bloqueado" si el sitio rechaza
     clientes automatizados (403). La próxima consulta lo usa directo, sin volver a descubrir.
  Nunca evade bloqueos ni simula un navegador humano.

obtener_tipo_cambio: cotización pública (open.er-api.com, sin clave) con fuente y fecha, para
  comparar contra competidores de otro país sin que el usuario tenga que informar el tipo de cambio.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from tools import fuentes
from tools.matching import normalizar

PAISES = {
    "argentina": ("Argentina", "ARS"), "mexico": ("México", "MXN"), "peru": ("Perú", "PEN"),
    "colombia": ("Colombia", "COP"), "chile": ("Chile", "CLP"), "uruguay": ("Uruguay", "UYU"),
    "brasil": ("Brasil", "BRL"), "paraguay": ("Paraguay", "PYG"), "bolivia": ("Bolivia", "BOB"),
    "ecuador": ("Ecuador", "USD"), "espana": ("España", "EUR"), "estados unidos": ("Estados Unidos", "USD"),
}
TLD_PAIS = {"ar": "argentina", "mx": "mexico", "pe": "peru", "co": "colombia", "cl": "chile", "uy": "uruguay",
            "br": "brasil", "py": "paraguay", "bo": "bolivia", "ec": "ecuador", "es": "espana"}
NO_OFICIALES = ("wikipedia", "facebook", "instagram", "linkedin", "youtube", "twitter", "x.com", "tiktok",
                "mercadolibre", "google", "tripadvisor", "glassdoor", "indeed", "infobae", "clarin",
                "lanacion", "reddit", "pinterest", "apps.apple", "play.google", "rappi", "pedidosya")
# Rutas de búsqueda habituales para probar el scraping ({q} = término de prueba).
PLANTILLAS_BUSQUEDA = ["/buscar?q={q}", "/search?q={q}", "/search/{q}", "/busca?q={q}",
                       "/{q}?_q={q}&map=ft", "/catalogsearch/result/?q={q}"]
TERMINO_PRUEBA = "leche"


def _clave(nombre: str) -> str:
    return normalizar(nombre).replace(" ", "")


def _pais(pais: str, base: str = "") -> tuple[str, str] | None:
    p = normalizar(pais or "")
    if p in PAISES:
        return PAISES[p]
    tld = urllib.parse.urlparse(base).netloc.rsplit(".", 1)[-1]
    return PAISES.get(TLD_PAIS.get(tld, ""))


def _base(url: str) -> str:
    u = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    return f"https://{u.netloc}"


def _candidatos_web(nombre: str, pais: str) -> list[str]:
    """Sitios candidatos a ser la tienda online oficial, según una búsqueda web."""
    from tools.tools import web_search

    res = web_search(f"{nombre} supermercado {pais} tienda online sitio oficial", max_results=8)
    if isinstance(res, str):  # error de búsqueda
        return []
    token = _clave(nombre)
    bases = []
    for r in res:
        href = r.get("href", "")
        net = urllib.parse.urlparse(href).netloc.lower()
        if not net or any(n in net for n in NO_OFICIALES):
            continue
        bases.append(_base(href))
    bases = list(dict.fromkeys(bases))
    # Primero los dominios que contienen el nombre del supermercado.
    return sorted(bases, key=lambda b: token[:6] not in b.replace("-", "").replace(".", ""))


def _probar_vtex(base: str) -> tuple[str, str]:
    """('ok'|'bloqueado'|'no', detalle)"""
    url = f"{base}/api/catalog_system/pub/products/search?ft={TERMINO_PRUEBA}&_from=0&_to=1"
    try:
        data = fuentes.http_get_json(url, reintentos=0)
    except PermissionError as e:
        return "bloqueado", f"robots.txt no permite el acceso: {e}"
    except urllib.error.HTTPError as e:
        return ("bloqueado" if e.code in (401, 403) else "no"), f"API VTEX: HTTP {e.code}"
    except Exception as e:
        return "no", f"API VTEX: {type(e).__name__}"
    ok = isinstance(data, list) and data and isinstance(data[0], dict) and "items" in data[0]
    return ("ok", "API VTEX responde") if ok else ("no", "API VTEX sin resultados")


def _probar_scraping(base: str) -> tuple[str, dict | str]:
    """('ok', cfg) | ('bloqueado', detalle) | ('no', detalle)"""
    from tools.scraper_web import _http_get_texto, _productos_jsonld, _productos_nextdata

    bloqueos = 0
    for plantilla in PLANTILLAS_BUSQUEDA:
        url = base + plantilla.format(q=TERMINO_PRUEBA)
        try:
            html = _http_get_texto(url)
        except PermissionError:
            return "bloqueado", "robots.txt no permite la página de búsqueda"
        except urllib.error.HTTPError as e:
            bloqueos += e.code in (401, 403, 429)
            continue
        except Exception:
            continue
        for estrategia, extraer in (("jsonld", _productos_jsonld), ("nextdata", _productos_nextdata)):
            if extraer(html):
                return "ok", {"url_busqueda": base + plantilla.replace("{q}", "{q}"), "estrategias": [estrategia]}
    if bloqueos >= 2:
        return "bloqueado", "la página de búsqueda responde 403/429 a clientes automatizados"
    return "no", "no se encontraron productos con datos estructurados (JSON-LD / __NEXT_DATA__)"


def descubrir_competidor(nombre: str, pais: str = "", url: str = "") -> dict:
    """
    Agrega un supermercado nuevo al registro de competidores (competidores.json). Usar cuando el
    usuario nombra un supermercado que no aparece en listar_competidores. Busca el sitio en la web,
    verifica robots.txt y detecta cómo consultarlo (API VTEX o scraping). Si el sitio bloquea el
    acceso automatizado lo registra como bloqueado y lo informa (no se evade).
    Args:
        nombre: str -> Nombre del supermercado, ej. "Chedraui".
        pais: str -> País donde opera, ej. "México" (define la moneda).
        url: str -> Opcional: sitio oficial si se conoce, ej. "https://www.chedraui.com.mx".
    Returns:
        Estado del descubrimiento: registrado | ya_registrado | bloqueado | no_encontrado | requiere_pais.
    """
    fuentes.cargar_registro()
    clave = _clave(nombre)
    if not clave:
        return {"estado": "no_encontrado", "motivo": "nombre vacío"}
    existente = fuentes.COMPETIDORES.get(clave) or next(
        (v for v in fuentes.COMPETIDORES.values() if _clave(v["nombre"]) == clave), None)
    if existente:
        return {**existente, "clave": clave, "estado": "ya_registrado", "estado_sitio": existente.get("estado")}

    bases = [_base(url)] if url else _candidatos_web(nombre, pais)
    if not bases:
        return {"estado": "no_encontrado", "motivo": f"no se encontró un sitio web para {nombre}"}

    intentos = []
    for base in bases[:3]:
        region = _pais(pais, base)
        vtex, det_v = _probar_vtex(base)
        intentos.append(f"{base}: {det_v}")
        if vtex == "ok":
            metodo, extra = "vtex", {}
        else:
            scr, cfg = _probar_scraping(base)
            intentos.append(f"{base}: scraping -> {cfg if isinstance(cfg, str) else 'ok'}")
            if scr == "ok":
                metodo, extra = "scraping", {"scraping": cfg}
            elif "bloqueado" in (vtex, scr):
                metodo, extra = "bloqueado", {"motivo": det_v if vtex == "bloqueado" else cfg}
            else:
                continue
        if not region:
            return {"estado": "requiere_pais", "sitio": base, "metodo": metodo,
                    "motivo": "no se pudo deducir el país/moneda del dominio; volver a llamar indicando pais"}
        datos = {"nombre": nombre.strip().title() if nombre.islower() else nombre.strip(), "base": base, "tipo": metodo, "pais": region[0], "moneda": region[1],
                 "estado": "bloqueado" if metodo == "bloqueado" else "activo",
                 "origen": f"descubierto automáticamente el {fuentes.ahora_iso()}", **extra}
        fuentes.guardar_competidor(clave, datos)
        estado = "bloqueado" if metodo == "bloqueado" else "registrado"
        return {**datos, "clave": clave, "estado": estado, "intentos": intentos}
    return {"estado": "no_encontrado", "motivo": "ningún sitio candidato tiene API o datos estructurados",
            "intentos": intentos}


# --- Tipo de cambio ---------------------------------------------------------------------------

_cache_fx: dict[tuple[str, str], tuple[float, dict]] = {}
FX_URL = "https://open.er-api.com/v6/latest/{origen}"


def obtener_tipo_cambio(moneda_origen: str, moneda_destino: str) -> dict:
    """
    Cotización de mercado entre dos monedas (ISO 4217, ej. MXN -> ARS), con fuente y fecha.
    Se usa para convertir precios de competidores de otro país a la moneda de Ragnar.
    Args:
        moneda_origen: str -> Moneda del competidor, ej. "MXN".
        moneda_destino: str -> Moneda de Ragnar, ej. "ARS".
    Returns:
        tipo_cambio (cuántas unidades de moneda_destino vale 1 de moneda_origen), fuente y fecha, o error.
    """
    o, d = moneda_origen.strip().upper(), moneda_destino.strip().upper()
    if o == d:
        return {"tipo_cambio": 1.0, "descripcion": f"1 {o} = 1 {d}"}
    cache = _cache_fx.get((o, d))
    if cache and time.time() - cache[0] < 3600:
        return cache[1]
    url = FX_URL.format(origen=urllib.parse.quote(o))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": fuentes.USER_AGENT})
        with urllib.request.urlopen(req, timeout=fuentes.TIMEOUT_SEG) as r:
            data = json.load(r)
        tasa = data["rates"][d]
    except Exception as e:
        return {"error": f"No se pudo obtener el tipo de cambio {o}->{d}: {type(e).__name__}: {e}"}
    res = {"tipo_cambio": round(float(tasa), 6), "descripcion": f"1 {o} = {tasa:.4f} {d}",
           "fuente": url, "actualizado": data.get("time_last_update_utc"), "fecha_consulta": fuentes.ahora_iso()}
    _cache_fx[(o, d)] = (time.time(), res)
    return res
