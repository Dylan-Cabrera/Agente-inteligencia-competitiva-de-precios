"""
Generación de informes (F07, F08, F09) y recuperación de informes previos (F10).
Consigna 4 / criterios de aceptación 9 y 10.

El informe se arma con código a partir de lo guardado en SQLite (no lo redacta el LLM):
así cada número del informe es exactamente el que se obtuvo de la fuente, con su URL y
fecha. El agente agrega encima su lectura/conclusiones.

Cada informe se indexa en la colección vectorial "informes" para poder recuperarlo luego
por similitud (RAG sobre análisis anteriores): "¿qué habíamos visto de yerbas la semana pasada?".
"""
from __future__ import annotations

import csv
import glob
import os
from datetime import datetime

from tools import historial
from tools.comparacion import UMBRAL_SIGNIFICATIVO, resumen_posicionamiento
from tools.matching import normalizar, tokens

INFORMES_DIR = os.path.join(historial.RAIZ, "informes")

ETIQUETAS = {
    "ragnar_mas_caro_significativo": "Ragnar más caro (significativo)",
    "ragnar_mas_caro": "Ragnar más caro",
    "ragnar_mas_barato_significativo": "Ragnar más barato (significativo)",
    "ragnar_mas_barato": "Ragnar más barato",
    "precio_similar": "Precio similar",
    "sin_coincidencia_confiable": "Sin coincidencia confiable",
    "sin_comparacion": "Sin comparación",
}


def _m(v, dec=2):
    if v in (None, ""):
        return "—"
    return f"{float(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v):
    return "—" if v in (None, "") else f"{float(v):+.1f} %".replace(".", ",")


def _link(url):
    return f"[ver]({url})" if url else "—"


def construir_markdown(filas: list[dict], titulo: str, consulta_id: str) -> str:
    comparadas = [f for f in filas if f.get("estado") == "comparado"]
    revision = [f for f in filas if f.get("estado") in ("requiere_revision", "sin_coincidencia_confiable")]
    no_enc = [f for f in filas if f.get("estado") in ("no_encontrado", "fuera_de_region")]
    errores = [f for f in filas if f.get("estado") in ("error_fuente", "acceso_no_permitido")]
    promos = [f for f in comparadas if f.get("precio_promocional") or f.get("promociones_condicionadas")]
    resumen = resumen_posicionamiento(comparadas)
    productos = {f["producto_id"] for f in filas}
    competidores = sorted({f["competidor"] for f in filas})
    fechas = sorted(f["fecha_consulta"] for f in filas if f.get("fecha_consulta"))

    L = [f"# {titulo}", "",
         f"- **Consulta:** `{consulta_id}`",
         f"- **Generado:** {datetime.now():%d/%m/%Y %H:%M}",
         f"- **Productos analizados:** {len(productos)}",
         f"- **Competidores consultados:** {', '.join(competidores) or '—'}",
         f"- **Fechas de consulta de precios:** {fechas[0][:16] if fechas else '—'} a {fechas[-1][:16] if fechas else '—'}",
         f"- **Umbral de diferencia significativa:** ±{UMBRAL_SIGNIFICATIVO:g} %", "",
         "## Resumen ejecutivo", ""]
    if resumen.get("observaciones_comparables"):
        n = resumen["observaciones_comparables"]
        L += [f"- Comparaciones válidas: **{n}** (producto × competidor).",
              f"- Ragnar más caro en **{resumen['ragnar_mas_caro']}**, más barato en "
              f"**{resumen['ragnar_mas_barato']}**, similar en **{resumen['precio_similar']}**.",
              f"- Diferencias significativas: **{resumen['diferencias_significativas']}**.",
              f"- Diferencia promedio de la competencia vs Ragnar: **{_pct(resumen['diferencia_porcentual_promedio'])}** "
              "(negativo = la competencia es más barata)."]
        if len(resumen["diferencia_promedio_por_categoria"]) > 1:
            L += ["- Por categoría: " + "; ".join(f"{k} {_pct(v)}" for k, v in
                                                  resumen["diferencia_promedio_por_categoria"].items())]
    else:
        L += ["- No hubo coincidencias confiables para comparar precios. No se informan diferencias."]
    L += [f"- Requieren revisión / sin coincidencia confiable: **{len(revision)}**; "
          f"no encontrados: **{len(no_enc)}**; errores de fuente: **{len(errores)}**.", ""]

    if comparadas:
        L += ["## Comparación de precios", "",
              "| Producto Ragnar | Precio Ragnar | Competidor | Producto encontrado | Precio regular | "
              "Precio promo | Base | Dif. abs. (regular) | Dif. % (regular) | Dif. % (promo) | Resultado | Fuente | Fecha |",
              "|---|---:|---|---|---:|---:|---|---:|---:|---:|---|---|---|"]
        for f in comparadas:
            base = f.get("base_comparacion") or ""
            normalizado = base.startswith("precio por ") and base != "precio por presentación"
            L.append(
                f"| {f['producto_id']} {f['producto']} {f['marca']} {f['presentacion']} | {_m(f['precio_ragnar'])} "
                f"| {f['competidor']} | {f.get('nombre_encontrado', '')} | {_m(f.get('precio_regular'))} "
                f"| {_m(f.get('precio_promocional'))} | {base}"
                f"{' (Ragnar ' + _m(f.get('precio_normalizado_ragnar')) + ' vs ' + _m(f.get('precio_normalizado_competidor')) + ')' if normalizado else ''} "
                f"| {_m(f.get('diferencia_absoluta_regular'))} | {_pct(f.get('diferencia_porcentual_regular'))} "
                f"| {_pct(f.get('diferencia_porcentual_promo'))} | {ETIQUETAS.get(f.get('clasificacion'), f.get('clasificacion'))} "
                f"| {_link(f.get('url'))} | {str(f.get('fecha_consulta', ''))[:16]} |")
        L.append("")

    if promos:
        L += ["## Promociones detectadas", ""]
        for f in promos:
            partes = []
            if f.get("precio_promocional"):
                partes.append(f"precio promocional {_m(f['precio_promocional'])} ({f.get('promocion') or 'descuento'})")
            cond = f.get("promociones_condicionadas")
            if cond:
                partes.append("condicionadas (no aplicadas al precio): " + "; ".join(cond if isinstance(cond, list) else [cond]))
            L.append(f"- **{f['competidor']}** — {f.get('nombre_encontrado')}: {'. '.join(partes)}. {_link(f.get('url'))}")
        L.append("")

    if revision:
        L += ["## Sin coincidencia confiable / requieren revisión humana", "",
              "No se compararon precios para estos casos porque el producto encontrado presenta "
              "diferencias relevantes o no se pudieron verificar sus características.", "",
              "| Producto Ragnar | Competidor | Mejor candidato | Confianza | Motivo | Fuente |",
              "|---|---|---|---|---|---|"]
        for f in revision:
            motivos = f.get("motivos") or []
            motivos = "; ".join(motivos) if isinstance(motivos, list) else motivos
            L.append(f"| {f['producto_id']} {f['producto']} {f['marca']} {f['presentacion']} | {f['competidor']} "
                     f"| {f.get('nombre_encontrado') or '—'} | {f.get('confianza') or '—'} | {motivos} | {_link(f.get('url'))} |")
        L.append("")

    if no_enc or errores:
        L += ["## No encontrados / fuentes con error", ""]
        for f in no_enc + errores:
            motivos = f.get("motivos") or []
            motivos = "; ".join(motivos) if isinstance(motivos, list) else motivos
            L.append(f"- {f['producto_id']} {f['producto']} {f['marca']} {f['presentacion']} — **{f['competidor']}**: "
                     f"{f['estado']}. {motivos} (consulta: {f.get('url_consulta') or '—'}, {str(f.get('fecha_consulta', ''))[:16]})")
        L.append("")

    L += ["## Metodología y limitaciones", "",
          "- Precios obtenidos de las APIs públicas de catálogo de cada supermercado (VTEX), respetando robots.txt "
          "y con un máximo de 1 consulta por segundo por sitio. Cada precio tiene enlace a la página del producto y fecha de consulta.",
          "- Diferencia = precio competidor − precio Ragnar; el porcentaje se calcula sobre el precio de Ragnar.",
          "- Solo se compara cuando coinciden marca, tipo, variante y contenido neto. Si cambia la presentación, se "
          "compara el precio por kg / litro / unidad y se indica en la columna *Base*.",
          "- El precio regular es el precio sin descuentos que publica el sitio; el promocional es el precio con descuento aplicado. Las promociones "
          "condicionadas (tarjetas, 2x1, 2da unidad) se informan aparte y no se aplican al cálculo.",
          "- Los precios online pueden diferir de los de sucursal y varían según la zona de entrega configurada en cada sitio.",
          "- Ragnar es una cadena ficticia: su catálogo tiene combinaciones de presentación/precio que pueden no existir "
          "en el mercado (ej. leche en gramos), lo que reduce las coincidencias.", ""]
    return "\n".join(L)


def generar_informe(consulta_id: str = "", producto_ids: str = "", competidor: str = "",
                    titulo: str = "") -> dict:
    """
    Genera el informe comparativo (Markdown + CSV) a partir de los precios guardados, con resumen
    ejecutivo, tabla de diferencias, promociones, productos sin coincidencia confiable, no
    encontrados, fuentes (URL) y fechas de consulta. Lo guarda en la carpeta informes/.
    Args:
        consulta_id: str -> Investigación a informar. Vacío = la última realizada.
        producto_ids: str -> Opcional: filtrar por ids separados por coma.
        competidor: str -> Opcional: filtrar por competidor.
        titulo: str -> Título del informe.
    Returns:
        Diccionario con las rutas del informe y su contenido en Markdown.
    """
    try:
        if not consulta_id and not producto_ids:
            consulta_id = historial.ultima_consulta_id() or ""
        filas = historial.obtener_observaciones(consulta_id, producto_ids, competidor)
        if not filas:
            return {"error": "No hay precios guardados para esos filtros. Primero usar investigar_productos."}
        titulo = titulo or "Informe comparativo de precios – Ragnar S.A."
        md = construir_markdown(filas, titulo, consulta_id or "varias")
        os.makedirs(INFORMES_DIR, exist_ok=True)
        nombre = f"informe_{(consulta_id or 'historial')}_{datetime.now():%Y%m%d-%H%M%S}"
        ruta_md = os.path.join(INFORMES_DIR, f"{nombre}.md")
        ruta_csv = os.path.join(INFORMES_DIR, f"{nombre}.csv")
        with open(ruta_md, "w", encoding="utf-8") as f:
            f.write(md)
        with open(ruta_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=historial.COLUMNAS, extrasaction="ignore")
            w.writeheader()
            w.writerows(filas)

        indexado = "no"
        try:
            from tools.tools import INFORMES_COLLECTION, index_data
            res = index_data(md, INFORMES_COLLECTION, {"archivo": os.path.basename(ruta_md),
                                                       "consulta_id": consulta_id})
            indexado = "si" if isinstance(res, list) else f"no ({str(res)[:60]})"
        except Exception as e:
            indexado = f"no ({str(e)[:60]})"
        return {"informe_markdown": os.path.relpath(ruta_md, historial.RAIZ),
                "informe_csv": os.path.relpath(ruta_csv, historial.RAIZ),
                "indexado_para_busqueda": indexado, "contenido": md[:8000]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def buscar_en_informes(query: str, k: int = 3) -> dict:
    """
    Busca en informes generados anteriormente (RAG sobre análisis previos) para retomar o
    comparar con investigaciones pasadas.
    Args:
        query: str -> Qué buscar (ej. "yerba Playadito Carrefour", "productos más caros en limpieza").
        k: int -> Cantidad de fragmentos.
    Returns:
        Fragmentos relevantes con el archivo de origen.
    """
    try:
        from tools.tools import INFORMES_COLLECTION, retrieval
        res = retrieval(query, INFORMES_COLLECTION, k)
        if isinstance(res, list) and res:
            return {"modo": "semantica", "fragmentos": res}
    except Exception:
        pass
    # Respaldo léxico si la base vectorial no está disponible.
    q = set(tokens(query))
    hallados = []
    for ruta in sorted(glob.glob(os.path.join(INFORMES_DIR, "*.md")), reverse=True):
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                if q and len(q & set(tokens(linea))) >= max(1, len(q) // 2):
                    hallados.append({"archivo": os.path.basename(ruta), "contenido": linea.strip()})
    return {"modo": "lexica", "fragmentos": hallados[: k * 5]} if hallados else \
        {"mensaje": f"No hay informes previos que mencionen '{normalizar(query)}'."}
