"""
Identificación y coincidencia de productos (F04) — consigna 4 / criterios de aceptación 4, 5 y 8.

El matching es DETERMINÍSTICO (reglas, no LLM): decide si un producto encontrado en un
competidor es el mismo artículo que el del catálogo de Ragnar. Prioriza la precisión sobre
la cobertura: ante la duda NO declara equivalencia (restricción: "No deberá comparar
productos sin verificar previamente sus características").

Niveles de confianza:
- "alto":  misma marca, mismo tipo de producto, misma variante y mismo contenido neto.
           -> se compara precio directo.
- "medio": misma marca y tipo, pero presentación distinta (se compara SOLO precio normalizado
           por unidad de medida) o atributos adicionales / variante no confirmada
           (queda para revisión humana, no se compara).
- "sin_coincidencia": marca distinta, tipo distinto, variante contradictoria o unidad de
           medida incompatible (ej. g vs ml).
"""
from __future__ import annotations

import re
import unicodedata

# --- Normalización de texto -------------------------------------------------------------

STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "para", "en", "y", "a", "al", "con", "por", "sin",
    "un", "una", "x", "e", "o",
}

# Palabras que no cambian el producto (no cuentan como "atributo adicional").
NEUTRAS = {
    "original", "clasico", "clasica", "tradicional", "sabor", "botella", "pet", "pack",
    "multipack", "paquete", "envase", "unidad", "unidades", "regular", "comun", "nuevo",
    "nueva", "formato", "bolsa", "cont", "contenido", "neto", "aprox", "grano", "tostado",
    "desinfectante", "blanqueador",  # descriptivos habituales de lavandina / cloro
    # Nota: "lata", "frasco", "estuche", "retornable", etc. NO son neutras: cambian el precio.
}

# Variantes conocidas: canónica -> sinónimos (texto normalizado, sin tildes).
VARIANTES = {
    "entera": ["entera", "entero", "3%"],
    "descremada": ["descremada", "descremado", "0%"],
    "light": ["light", "liviana", "liviano", "1%", "2%", "parcialmente"],
    "zero": ["zero", "sin azucar", "sin azucares", "diet"],
    "con palo": ["con palo"],
    "sin palo": ["sin palo"],
    "con hierbas": ["con hierbas", "hierbas"],
    "suave": ["suave"],
    "integral": ["integral"],
    "antisplash": ["antisplash", "anti splash", "anti-splash"],
    "gel": ["gel"],
    "aerosol": ["aerosol"],
    "roll on": ["roll on", "rollon", "roll-on"],
    "barra": ["barra"],
    "polvo": ["polvo"],
    "concentrado": ["concentrado", "concentrada"],
    "repuesto": ["repuesto", "doypack", "recarga"],
    "limon": ["limon"],
    "naranja": ["naranja"],
    "pomelo": ["pomelo"],
    "manzana": ["manzana"],
    "frutilla": ["frutilla"],
    "durazno": ["durazno"],
    "uva": ["uva"],
    "mandarina": ["mandarina"],
    "multifruta": ["multifruta"],
    "coco": ["coco"],
    "vainilla": ["vainilla"],
    "lavanda": ["lavanda"],
    "menta": ["menta"],
}

# Sinónimos del tipo de producto (token del catálogo -> alternativas aceptadas).
SINONIMOS_TIPO = {
    # Nombres regionales (Argentina -> México / Perú / Colombia / España). El 1er elemento es el propio.
    "lavandina": ["lavandina", "cloro", "blanqueador", "lejia"],
    "gaseosa": ["gaseosa", "refresco", "soda"],
    "jugo": ["jugo", "zumo", "nectar"],
    "pasta": ["pasta", "crema"],          # pasta dental / crema dental
    "higienico": ["higienico", "sanitario"],
    "esponja": ["esponja", "fibra"],
    "suavizante": ["suavizante", "suavizador"],
    "residuo": ["residuo", "consorcio", "basura"],
    "rollo": ["rollo", "toalla"],
    "cocina": ["cocina"],
    "detergente": ["detergente", "lavavajilla"],
    "desodorante": ["desodorante", "antitranspirante"],
    "energizante": ["energizante", "energetica"],
    "jabon": ["jabon"],
}

UNIDADES = {
    "kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
    "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "gramos": ("g", 1), "grm": ("g", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000), "litro": ("ml", 1000),
    "litros": ("ml", 1000), "ml": ("ml", 1), "cc": ("ml", 1), "cm3": ("ml", 1),
    "un": ("un", 1), "u": ("un", 1), "unid": ("un", 1), "unidades": ("un", 1),
    "rollos": ("un", 1), "panos": ("un", 1), "bolsas": ("un", 1), "hojas": ("un", 1),
}
_UNIT_RE = "|".join(sorted(UNIDADES, key=len, reverse=True))
_MEDIDA = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNIT_RE})\b")
_MULT_A = re.compile(r"\b(\d+)\s*x\s*(?=\d)")                    # "6 x 500 ml"
_MULT_B = re.compile(rf"\bx\s*(\d+)\b(?!\s*(?:[.,]\d|{_UNIT_RE})\b)")  # "multipack x3 90 g"


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, sin signos (se conservan % , . y -)."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    t = t.lower().replace("-", " ")
    t = re.sub(r"[^a-z0-9%.,\s]", " ", t)
    t = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", t)  # puntos/comas que no son decimales
    return re.sub(r"\s+", " ", t).strip()


def _compacta(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalizar(texto))


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("es") and not token.endswith("ces"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def tokens(texto: str) -> list[str]:
    return [_stem(t) for t in normalizar(texto).split()
            if t not in STOPWORDS and not re.fullmatch(r"[\d.,%]+", t) and len(t) > 1]


# --- Contenido neto ---------------------------------------------------------------------

def parsear_contenido(texto: str) -> dict | None:
    """
    Extrae el contenido neto de un nombre de producto y lo lleva a unidad base (g, ml, un).
    Ej.: "Gaseosa 1,75 lts" -> {cantidad: 1750, unidad: "ml"};
         "Jabón Dove Multipack x3 90 grs" -> {cantidad: 270, unidad: "g", multiplicador: 3}.
    """
    t = normalizar(texto)
    medidas = list(_MEDIDA.finditer(t))
    if not medidas:
        return None
    m = medidas[-1]
    valor = float(m.group(1).replace(",", "."))
    base, factor = UNIDADES[m.group(2)]
    mult = 1
    mm = _MULT_A.search(t) or _MULT_B.search(t)
    if mm and base != "un":
        mult = int(mm.group(1))
    return {"cantidad": round(valor * factor * mult, 3), "unidad": base, "multiplicador": mult,
            "texto": m.group(0)}


def contenido_catalogo(producto: dict) -> dict | None:
    """Contenido de un producto del catálogo (columnas contenido + unidad_medida)."""
    try:
        valor = float(str(producto["contenido"]).replace(",", "."))
        base, factor = UNIDADES[normalizar(producto["unidad_medida"])]
        return {"cantidad": valor * factor, "unidad": base, "multiplicador": 1}
    except (KeyError, ValueError):
        return parsear_contenido(producto.get("presentacion", ""))


# --- Variantes --------------------------------------------------------------------------

def variantes(texto: str) -> set[str]:
    t = f" {normalizar(texto)} "
    encontradas = set()
    for canon, sinonimos in VARIANTES.items():
        if any(f" {s} " in t for s in sinonimos):
            encontradas.add(canon)
    # "sin palo" contiene "palo" pero no "con palo"; "sin azucar" no es "azucar".
    return encontradas


def _palabras_variante() -> set[str]:
    out = set()
    for sinonimos in VARIANTES.values():
        for s in sinonimos:
            out.update(_stem(w) for w in normalizar(s).split())
    return out


_PALABRAS_VARIANTE = _palabras_variante()


# --- Coincidencia -----------------------------------------------------------------------

def _marca_coincide(marca_cat: str, marca_enc: str, nombre_enc: str) -> bool:
    mc = _compacta(marca_cat)
    if not mc:
        return False
    return mc == _compacta(marca_enc) or mc in _compacta(nombre_enc)


def _token_presente(tok: str, tokens_enc: set[str]) -> bool:
    return any(_stem(s) in tokens_enc for s in SINONIMOS_TIPO.get(tok, [tok]))


def evaluar_coincidencia(producto: dict, nombre_encontrado: str, marca_encontrada: str = "") -> dict:
    """
    Compara un producto del catálogo de Ragnar con un producto encontrado en un competidor.
    Args:
        producto: dict -> fila del catálogo (producto, marca, categoria, contenido, unidad_medida...).
        nombre_encontrado: str -> nombre del producto en el sitio del competidor.
        marca_encontrada: str -> marca informada por el competidor (si la hay).
    Returns:
        dict con confianza ("alto" | "medio" | "sin_coincidencia"), comparable
        ("directo" | "normalizado" | "no"), motivos y contenidos detectados.
    """
    motivos: list[str] = []
    nombre_cat = producto.get("producto", "")
    marca_cat = producto.get("marca", "")

    # 1) Marca (regla dura)
    if not _marca_coincide(marca_cat, marca_encontrada, nombre_encontrado):
        return _res("sin_coincidencia", "no",
                    [f"marca distinta (Ragnar: {marca_cat}; encontrado: {marca_encontrada or '?'})"])

    # 2) Tipo de producto (tokens del nombre del catálogo presentes en el encontrado)
    tok_cat = [t for t in tokens(nombre_cat) if t not in _PALABRAS_VARIANTE] or tokens(nombre_cat)
    tok_enc = set(tokens(nombre_encontrado))
    presentes = [t for t in tok_cat if _token_presente(t, tok_enc)]
    cobertura = len(presentes) / len(tok_cat) if tok_cat else 0
    if cobertura < 0.5:
        return _res("sin_coincidencia", "no",
                    [f"tipo de producto distinto (faltan: {', '.join(set(tok_cat) - set(presentes))})"])
    if cobertura < 1:
        motivos.append(f"nombre parcialmente coincidente (faltan: {', '.join(set(tok_cat) - set(presentes))})")

    # 3) Variantes (regla dura si se contradicen)
    v_cat, v_enc = variantes(nombre_cat), variantes(nombre_encontrado)
    if v_cat and v_enc and not (v_cat & v_enc):
        return _res("sin_coincidencia", "no",
                    [f"variante distinta (Ragnar: {', '.join(sorted(v_cat))}; encontrado: {', '.join(sorted(v_enc))})"])
    if v_enc - v_cat:
        motivos.append(f"variante no especificada en el catálogo: {', '.join(sorted(v_enc - v_cat))}")
    if v_cat - v_enc:
        motivos.append(f"no se pudo confirmar la variante: {', '.join(sorted(v_cat - v_enc))}")

    # 4) Atributos adicionales no reconocidos (posible variante desconocida)
    tok_marca = set(tokens(marca_cat)) | set(tokens(marca_encontrada))
    ignorar = set(tok_cat) | tok_marca | NEUTRAS | _PALABRAS_VARIANTE | set(UNIDADES) \
        | {s for t in tok_cat for s in map(_stem, SINONIMOS_TIPO.get(t, []))}
    extras = sorted({t for t in tok_enc if t not in ignorar and _stem(t) not in ignorar and not re.search(r"\d", t)})
    if extras:
        motivos.append(f"atributos adicionales en el producto encontrado: {', '.join(extras)}")

    # 5) Contenido neto y unidad de medida
    c_cat = contenido_catalogo(producto)
    c_enc = parsear_contenido(nombre_encontrado)
    contenido = {"ragnar": c_cat, "encontrado": c_enc}
    if not c_enc or not c_cat:
        motivos.append("no se pudo verificar el contenido neto")
        return _res("medio", "no", motivos, contenido)
    if c_cat["unidad"] != c_enc["unidad"]:
        return _res("sin_coincidencia", "no",
                    motivos + [f"unidad de medida incompatible ({c_cat['unidad']} vs {c_enc['unidad']})"],
                    contenido)

    misma_presentacion = abs(c_cat["cantidad"] - c_enc["cantidad"]) < 0.5
    if motivos:  # hay dudas sobre la identidad del producto -> revisión humana
        if not misma_presentacion:
            motivos.append(f"presentación distinta ({_fmt(c_cat)} vs {_fmt(c_enc)})")
        return _res("medio", "no", motivos, contenido)
    if not misma_presentacion:
        return _res("medio", "normalizado",
                    [f"mismo producto en otra presentación ({_fmt(c_cat)} vs {_fmt(c_enc)}): "
                     "se compara solo el precio por unidad de medida"], contenido)
    return _res("alto", "directo", ["marca, tipo, variante y contenido neto coinciden"], contenido)


def _fmt(c: dict) -> str:
    return f"{c['cantidad']:g} {c['unidad']}"


def _res(confianza: str, comparable: str, motivos: list[str], contenido: dict | None = None) -> dict:
    return {"confianza": confianza, "comparable": comparable, "motivos": motivos,
            "contenido": contenido or {}}


ORDEN_CONFIANZA = {"alto": 3, "medio": 2, "sin_coincidencia": 0}


def puntaje(evaluacion: dict) -> int:
    """Orden de preferencia entre candidatos: directo > normalizado > revisión > descartado."""
    base = ORDEN_CONFIANZA[evaluacion["confianza"]] * 10
    return base + {"directo": 3, "normalizado": 2, "no": 0}[evaluacion["comparable"]]
