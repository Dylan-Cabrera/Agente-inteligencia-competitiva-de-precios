"""
Scraper web genérico para competidores SIN API estructurada (complementa tools/fuentes.py).

Estrategias de extracción, de la más a la menos frágil (se prueban en orden y se usa la primera que rinde):
  1. "jsonld"   -> <script type="application/ld+json"> con Product / ItemList (schema.org). Estándar, estable.
                   Ej.: Vivanda (Perú).
  2. "nextdata" -> estado embebido de Next.js (<script id="__NEXT_DATA__">) recorrido de forma genérica
                   buscando objetos "producto" (nombre + precios). Ej.: Tottus (Perú).
  3. "browser"  -> render con Playwright/Chromium para SPAs (Angular/React sin SSR), si está instalado.
                   Ej.: Coto Digital (Argentina). Opcional: sin Playwright devuelve error_fuente explícito.

Acceso respetuoso (igual que fuentes.py): robots.txt, User-Agent identificable, rate limit por dominio.
NO se evaden bloqueos: si el sitio responde 403/captcha se informa "error_fuente"/"acceso_no_permitido"
(ej. La Anónima y Libertad bloquean clientes automatizados -> quedan fuera, no se simulan navegadores humanos).
Devuelve el MISMO formato que fuentes.buscar_en_competidor (precio regular vs promocional, URL, fecha).
"""
from __future__ import annotations

import json
import re
import urllib.parse

from tools import fuentes

_RE_JSONLD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
_RE_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _a_float(valor) -> float | None:
    """'1.234,50' / '9.50' / ['11'] / 11 -> float."""
    if isinstance(valor, (list, tuple)):
        valor = valor[0] if valor else None
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = re.sub(r"[^\d.,]", "", str(valor))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) <= 2 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _http_get_texto(url: str) -> str:
    """GET respetuoso (robots + rate limit + reintentos) que devuelve HTML."""
    import time
    import urllib.error
    import urllib.request

    if not fuentes._permitido(url):
        raise PermissionError(f"robots.txt no permite acceder a {url} (o no se pudo verificar)")
    dominio = urllib.parse.urlparse(url).netloc
    for intento in range(3):
        fuentes._esperar_turno(dominio)
        req = urllib.request.Request(url, headers={"User-Agent": fuentes.USER_AGENT,
                                                    "Accept": "text/html,application/xhtml+xml"})
        try:
            with urllib.request.urlopen(req, timeout=fuentes.TIMEOUT_SEG) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and intento < 2:
                time.sleep(2 ** (intento + 1))
                continue
            raise  # 403/404: no se insiste ni se evade el bloqueo
        except (urllib.error.URLError, TimeoutError):
            if intento < 2:
                time.sleep(2 ** (intento + 1))
                continue
            raise
    return ""


# --- Estrategia 1: JSON-LD ----------------------------------------------------------------

def _productos_jsonld(html: str) -> list[dict]:
    out = []

    def visitar(o):
        if isinstance(o, list):
            for x in o:
                visitar(x)
        elif isinstance(o, dict):
            tipo = o.get("@type")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if "Product" in tipos and o.get("name"):
                out.append(o)
            for v in o.values():
                if isinstance(v, (dict, list)):
                    visitar(v)

    for m in _RE_JSONLD.finditer(html):
        try:
            visitar(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    res = []
    for p in out:
        ofertas = p.get("offers") or {}
        ofertas = ofertas if isinstance(ofertas, list) else [ofertas]
        precios = [_a_float(o.get("price") or o.get("lowPrice")) for o in ofertas if isinstance(o, dict)]
        precios = [x for x in precios if x]
        if not precios:
            continue
        o0 = next(o for o in ofertas if isinstance(o, dict))
        marca = p.get("brand")
        marca = marca.get("name", "") if isinstance(marca, dict) else (marca or "")
        res.append({"nombre": p["name"], "marca": marca, "regular": min(precios), "promo": None,
                    "url": p.get("url") or p.get("@id") or "",
                    "disponible": "InStock" in str(o0.get("availability", "InStock")),
                    "moneda": o0.get("priceCurrency")})
    return res


# --- Estrategia 2: __NEXT_DATA__ (Tottus y similares) --------------------------------------

def _productos_nextdata(html: str) -> list[dict]:
    m = _RE_NEXT.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    res = []

    def visitar(o):
        if isinstance(o, dict):
            nombre = o.get("displayName") or o.get("productName")
            if nombre and isinstance(o.get("prices"), list):
                actual = regular = None
                for pr in o["prices"]:
                    valor = _a_float(pr.get("price"))
                    if valor is None:
                        continue
                    if pr.get("crossed"):
                        regular = valor          # precio tachado = precio normal
                    elif actual is None:
                        actual = valor           # internetPrice/eventPrice = precio vigente
                if actual:
                    disp = o.get("availability")
                    res.append({"nombre": nombre, "marca": o.get("brand", ""),
                                "regular": regular or actual, "promo": actual if regular and actual < regular else None,
                                "url": o.get("url", ""), "disponible": disp is not False,
                                "moneda": None,
                                "promocion": (o.get("discountBadge") or {}).get("label")})
                return
            for v in o.values():
                visitar(v)
        elif isinstance(o, list):
            for v in o:
                visitar(v)

    visitar(data)
    return res


# --- Estrategia 3: navegador (opcional) ------------------------------------------------------

def _html_con_navegador(url: str, espera_selector: str | None = None, espera_ms: int = 4000) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright no instalado (pip install playwright && playwright install chromium)") from e
    if not fuentes._permitido(url):
        raise PermissionError(f"robots.txt no permite acceder a {url} (o no se pudo verificar)")
    fuentes._esperar_turno(urllib.parse.urlparse(url).netloc)
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=True)
        try:
            ctx = nav.new_context(user_agent=fuentes.USER_AGENT, locale="es-AR")
            pag = ctx.new_page()
            pag.goto(url, wait_until="domcontentloaded", timeout=int(fuentes.TIMEOUT_SEG * 2000))
            if espera_selector:
                pag.wait_for_selector(espera_selector, timeout=15000)
            else:
                pag.wait_for_timeout(espera_ms)
            return pag.content()
        finally:
            nav.close()


def _productos_dom(html: str, cfg: dict) -> list[dict]:
    """Extracción por selectores CSS del DOM renderizado (cfg['selectores'])."""
    from lxml import html as lh

    sel = cfg["selectores"]
    doc = lh.fromstring(html)
    res = []
    for card in doc.cssselect(sel["card"]):
        def txt(css):
            n = card.cssselect(css) if css else []
            return n[0].text_content().strip() if n else ""
        nombre = txt(sel["nombre"])
        regular = _a_float(txt(sel.get("precio_regular", "")))
        actual = _a_float(txt(sel["precio"]))
        if not nombre or not (actual or regular):
            continue
        enlace = card.cssselect(sel.get("link", "a"))
        href = enlace[0].get("href", "") if enlace else ""
        res.append({"nombre": nombre, "marca": txt(sel.get("marca", "")),
                    "regular": regular or actual,
                    "promo": actual if regular and actual and actual < regular else None,
                    "url": href, "disponible": True, "moneda": None})
    return res


# --- API pública ----------------------------------------------------------------------------

def buscar_scraping(clave: str, consulta: str, max_resultados: int = 8) -> dict:
    """Busca una consulta en un competidor 'scraping'. Nunca lanza: devuelve estado + resultados."""
    comp = fuentes.COMPETIDORES[clave]
    cfg = comp["scraping"]
    fecha = fuentes.ahora_iso()
    url = cfg["url_busqueda"].format(q=urllib.parse.quote(consulta), q_plus=urllib.parse.quote_plus(consulta))
    try:
        crudos = []
        for estrategia in cfg["estrategias"]:
            if estrategia == "browser":
                html = _html_con_navegador(url, cfg.get("espera_selector"))
                crudos = _productos_jsonld(html) or _productos_nextdata(html) or (
                    _productos_dom(html, cfg) if cfg.get("selectores") else [])
            else:
                html = _http_get_texto(url)
                crudos = _productos_jsonld(html) if estrategia == "jsonld" else _productos_nextdata(html)
            if crudos:
                break
    except PermissionError as e:
        return {"competidor": comp["nombre"], "estado": "acceso_no_permitido", "detalle": str(e),
                "resultados": [], "url_consulta": url, "fecha_consulta": fecha}
    except Exception as e:
        return {"competidor": comp["nombre"], "estado": "error_fuente", "detalle": f"{type(e).__name__}: {e}",
                "resultados": [], "url_consulta": url, "fecha_consulta": fecha}
    resultados = []
    for p in crudos[:max_resultados]:
        link = p["url"]
        if link.startswith("/"):
            link = comp["base"] + link
        promo = p.get("promo")
        resultados.append({
            "competidor": comp["nombre"], "nombre_encontrado": p["nombre"], "marca": p.get("marca", ""),
            "precio_regular": round(p["regular"], 2),
            "precio_promocional": round(promo, 2) if promo else None,
            "promocion": (f"{round((1 - promo / p['regular']) * 100)}% de descuento sobre precio regular"
                          if promo else None),
            "promociones_condicionadas": None,  # el HTML público no expone promos por tarjeta
            "disponible": p.get("disponible", True),
            "moneda": p.get("moneda") or comp["moneda"], "pais": comp["pais"], "url": link or url,
            "fuente": f"Scraping web ({'/'.join(cfg['estrategias'])}) de {comp['nombre']}",
            "fecha_consulta": fecha})
    return {"competidor": comp["nombre"], "estado": "ok" if resultados else "sin_resultados",
            "resultados": resultados, "url_consulta": url, "fecha_consulta": fecha}
