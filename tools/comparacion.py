"""
Comparación de precios (F05), promociones (F06) y análisis de resultados (F07).
Consigna 4 / criterios de aceptación 5, 6 y 7.

Todos los cálculos se hacen con código (no los hace el LLM) para que sean exactos y
reproducibles.

Convenciones (documentadas también en el README):
- diferencia_absoluta  = precio_competidor - precio_ragnar
- diferencia_porcentual = diferencia_absoluta / precio_ragnar * 100   (base = precio Ragnar)
  -> positivo: el competidor es más caro (Ragnar más barato);
     negativo: el competidor es más barato (Ragnar más caro).
- Normalización: precio por kg (g), por litro (ml) o por unidad (un).
- Diferencia significativa: |diferencia_porcentual| >= UMBRAL_DIFERENCIA_SIGNIFICATIVA (10 % por defecto).
"""
from __future__ import annotations

import os

UMBRAL_SIGNIFICATIVO = float(os.environ.get("UMBRAL_DIFERENCIA_SIGNIFICATIVA", "10"))

BASES = {"g": (1000, "kg"), "ml": (1000, "litro"), "un": (1, "unidad")}


def precio_normalizado(precio: float, cantidad: float, unidad: str) -> dict | None:
    """Precio por kg / litro / unidad. Devuelve None si no se puede normalizar."""
    if not precio or not cantidad or unidad not in BASES:
        return None
    factor, etiqueta = BASES[unidad]
    return {"precio": round(precio / cantidad * factor, 2), "por": etiqueta}


def diferencia(precio_ragnar: float, precio_competidor: float) -> dict:
    """Diferencia absoluta y porcentual (base: precio de Ragnar)."""
    if precio_ragnar is None or precio_competidor is None or precio_ragnar <= 0:
        return {"diferencia_absoluta": None, "diferencia_porcentual": None}
    dif = precio_competidor - precio_ragnar
    return {"diferencia_absoluta": round(dif, 2),
            "diferencia_porcentual": round(dif / precio_ragnar * 100, 2)}


def clasificar(diferencia_porcentual: float | None, umbral: float = UMBRAL_SIGNIFICATIVO) -> str:
    """Clasificación desde el punto de vista de Ragnar."""
    if diferencia_porcentual is None:
        return "sin_comparacion"
    if abs(diferencia_porcentual) < 0.5:
        return "precio_similar"
    sufijo = "_significativo" if abs(diferencia_porcentual) >= umbral else ""
    # competidor más barato => Ragnar tiene precio superior
    return ("ragnar_mas_caro" if diferencia_porcentual < 0 else "ragnar_mas_barato") + sufijo


def convertir_moneda(precio: float, moneda_origen: str, moneda_destino: str,
                     tipo_cambio: float | None) -> float | None:
    """Convierte solo si hay tipo de cambio explícito; nunca supone uno."""
    if moneda_origen == moneda_destino:
        return precio
    if not tipo_cambio:
        return None
    return round(precio * tipo_cambio, 2)


def comparar(producto: dict, oferta: dict, evaluacion: dict, tipo_cambio: float | None = None) -> dict:
    """
    Compara el precio de Ragnar con la oferta de un competidor según el resultado del matching.
    - comparable == "directo": precio contra precio (y también normalizado).
    - comparable == "normalizado": solo precio por unidad de medida (presentaciones distintas).
    - comparable == "no": no se calcula ninguna diferencia.
    Compara por separado el precio regular y el promocional (si existe).
    """
    precio_ragnar = float(producto["precio"])
    moneda_ragnar = producto.get("moneda", "ARS")
    out = {"comparable": evaluacion["comparable"], "moneda": moneda_ragnar,
           "umbral_significativo_pct": UMBRAL_SIGNIFICATIVO}

    if evaluacion["comparable"] == "no":
        out.update(clasificacion="sin_coincidencia_confiable" if evaluacion["confianza"] != "alto"
                   else "sin_comparacion")
        return out

    regular = convertir_moneda(oferta.get("precio_regular"), oferta.get("moneda", moneda_ragnar),
                               moneda_ragnar, tipo_cambio)
    promo = convertir_moneda(oferta.get("precio_promocional"), oferta.get("moneda", moneda_ragnar),
                             moneda_ragnar, tipo_cambio) if oferta.get("precio_promocional") else None
    if regular is None:
        out.update(clasificacion="sin_comparacion",
                   motivo=f"moneda distinta ({oferta.get('moneda')}) sin tipo de cambio informado")
        return out

    c_rag = evaluacion["contenido"].get("ragnar") or {}
    c_enc = evaluacion["contenido"].get("encontrado") or {}
    norm_rag = precio_normalizado(precio_ragnar, c_rag.get("cantidad"), c_rag.get("unidad"))
    norm_reg = precio_normalizado(regular, c_enc.get("cantidad"), c_enc.get("unidad"))
    norm_promo = precio_normalizado(promo, c_enc.get("cantidad"), c_enc.get("unidad")) if promo else None
    out.update(precio_normalizado_ragnar=norm_rag, precio_normalizado_competidor=norm_reg,
               precio_normalizado_competidor_promo=norm_promo)

    if evaluacion["comparable"] == "directo":
        base_rag, base_reg, base_promo = precio_ragnar, regular, promo
        out["base_comparacion"] = "precio por presentación"
    else:
        if not (norm_rag and norm_reg):
            out.update(clasificacion="sin_comparacion", motivo="no se pudo normalizar el precio")
            return out
        base_rag, base_reg = norm_rag["precio"], norm_reg["precio"]
        base_promo = norm_promo["precio"] if norm_promo else None
        out["base_comparacion"] = f"precio por {norm_rag['por']}"

    d_reg = diferencia(base_rag, base_reg)
    d_promo = diferencia(base_rag, base_promo) if base_promo else {}
    out.update(
        diferencia_absoluta_regular=d_reg["diferencia_absoluta"],
        diferencia_porcentual_regular=d_reg["diferencia_porcentual"],
        diferencia_absoluta_promo=d_promo.get("diferencia_absoluta"),
        diferencia_porcentual_promo=d_promo.get("diferencia_porcentual"),
        clasificacion=clasificar(d_reg["diferencia_porcentual"]),
        clasificacion_promo=clasificar(d_promo.get("diferencia_porcentual")) if d_promo else None,
    )
    return out


def resumen_posicionamiento(filas: list[dict]) -> dict:
    """
    Posicionamiento general de Ragnar a partir de observaciones comparables (F07).
    Cada fila debe tener: producto_id, categoria, clasificacion, diferencia_porcentual_regular.
    """
    comparables = [f for f in filas if f.get("diferencia_porcentual_regular") is not None]
    total = len(comparables)
    if not total:
        return {"observaciones_comparables": 0}

    def cuenta(prefijo):
        return sum(1 for f in comparables if str(f["clasificacion"]).startswith(prefijo))

    por_categoria: dict[str, list[float]] = {}
    for f in comparables:
        por_categoria.setdefault(f.get("categoria") or "Sin categoría", []).append(
            f["diferencia_porcentual_regular"])
    promedio = sum(f["diferencia_porcentual_regular"] for f in comparables) / total
    return {
        "observaciones_comparables": total,
        "ragnar_mas_caro": cuenta("ragnar_mas_caro"),
        "ragnar_mas_barato": cuenta("ragnar_mas_barato"),
        "precio_similar": cuenta("precio_similar"),
        "diferencias_significativas": sum(1 for f in comparables
                                          if str(f["clasificacion"]).endswith("_significativo")),
        "diferencia_porcentual_promedio": round(promedio, 2),
        "diferencia_promedio_por_categoria": {k: round(sum(v) / len(v), 2)
                                              for k, v in sorted(por_categoria.items())},
        "lectura": "diferencia positiva = la competencia es más cara que Ragnar",
    }
