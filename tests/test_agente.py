"""
Tests offline (sin red ni Ollama) de los criterios de aceptación verificables con código.
Ejecutar:  env/bin/python -m pytest -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import fuentes, historial  # noqa: E402
from tools.catalogo import consultar_catalogo, obtener_producto  # noqa: E402
from tools.comparacion import clasificar, comparar, diferencia, precio_normalizado  # noqa: E402
from tools.matching import evaluar_coincidencia, parsear_contenido  # noqa: E402


def P(nombre, marca, contenido, unidad, precio=1000.0):
    return {"producto_id": "RAG-99999", "producto": nombre, "marca": marca, "categoria": "Bebidas",
            "presentacion": f"{contenido} {unidad}", "contenido": str(contenido),
            "unidad_medida": unidad, "precio": precio, "moneda": "ARS", "pais": "Argentina"}


# --- Criterio 2: consulta del catálogo (solo lectura) -----------------------------------

def test_catalogo_por_id_y_filtros():
    r = consultar_catalogo("RAG-00001")
    assert r["modo"] == "id_exacto" and r["productos"][0]["producto"] == "Galletitas dulces"
    r = consultar_catalogo(categoria="Bebidas", marca="Coca-Cola", k=50)
    assert r["productos"] and all(p["marca"] == "Coca-Cola" for p in r["productos"])


def test_catalogo_producto_inexistente():
    assert consultar_catalogo("RAG-12345").get("no_encontrados") == ["RAG-12345"]
    r = consultar_catalogo("caviar beluga", categoria="Alimentos")
    assert not r["productos"] or "mensaje" in r


def test_catalogo_no_se_modifica():
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "catalogo_ragnar.csv")
    antes = os.path.getmtime(ruta)
    consultar_catalogo("leche")
    assert os.path.getmtime(ruta) == antes


# --- Criterios 4, 5 y 8: coincidencia de productos ---------------------------------------

@pytest.mark.parametrize("texto,cantidad,unidad", [
    ("Gaseosa cola Coca Cola sabor original 1,75 lts", 1750, "ml"),
    ("Yerba mate Playadito suave con palo 1 kg.", 1000, "g"),
    ("Jabón de Tocador Dove Original Multipack x3 90 grs", 270, "g"),
    ("Agua mineral 6 x 500 ml", 3000, "ml"),
    ("Lavandina 500cc", 500, "ml"),
    ("Rollo de cocina Elite 3 un", 3, "un"),
])
def test_parseo_contenido(texto, cantidad, unidad):
    c = parsear_contenido(texto)
    assert c["cantidad"] == cantidad and c["unidad"] == unidad


def test_mismo_producto_confianza_alta():
    ev = evaluar_coincidencia(P("Gaseosa cola", "Coca-Cola", 1750, "ml"),
                              "Gaseosa cola Coca Cola sabor original 1,75 lts", "Coca Cola")
    assert ev["confianza"] == "alto" and ev["comparable"] == "directo"


def test_marca_distinta_se_descarta():
    ev = evaluar_coincidencia(P("Yerba mate", "Playadito", 500, "g"), "Yerba mate Taragüi 500 g", "Taragüi")
    assert ev["confianza"] == "sin_coincidencia"


def test_variante_contradictoria_se_descarta():
    ev = evaluar_coincidencia(P("Leche entera", "La Serenísima", 1000, "ml"),
                              "Leche La serenisima liviana 1% 1L", "La Serenísima")
    assert ev["confianza"] == "sin_coincidencia"


def test_variante_extra_requiere_revision():
    ev = evaluar_coincidencia(P("Gaseosa cola", "Coca-Cola", 1750, "ml"),
                              "Gaseosa cola Coca Cola Zero 1,75 lts", "Coca Cola")
    assert ev["confianza"] == "medio" and ev["comparable"] == "no"


def test_presentacion_distinta_900g_vs_1kg_solo_normalizado():
    ev = evaluar_coincidencia(P("Yerba mate", "Playadito", 900, "g"), "Yerba mate Playadito 1 kg", "Playadito")
    assert ev["comparable"] == "normalizado"


def test_unidad_incompatible():
    ev = evaluar_coincidencia(P("Leche entera", "La Serenísima", 250, "g"),
                              "Leche La serenisima clásica 3% 1L", "La Serenísima")
    assert ev["confianza"] == "sin_coincidencia"


# --- Criterios 6 y 7: cálculos y promociones --------------------------------------------

def test_diferencias():
    d = diferencia(1000, 1150)
    assert d == {"diferencia_absoluta": 150, "diferencia_porcentual": 15.0}
    assert diferencia(2000, 1500)["diferencia_porcentual"] == -25.0
    assert clasificar(-25) == "ragnar_mas_caro_significativo"
    assert clasificar(5) == "ragnar_mas_barato"
    assert clasificar(0.2) == "precio_similar"


def test_normalizacion():
    assert precio_normalizado(900, 900, "g") == {"precio": 1000.0, "por": "kg"}
    assert precio_normalizado(3000, 1500, "ml") == {"precio": 2000.0, "por": "litro"}


def test_comparar_regular_y_promo_por_separado():
    prod = P("Yerba mate", "Playadito", 900, "g", precio=4500)
    ev = evaluar_coincidencia(prod, "Yerba mate Playadito 1 kg", "Playadito")
    r = comparar(prod, {"precio_regular": 6000, "precio_promocional": 4000, "moneda": "ARS"}, ev)
    # Ragnar: 5000/kg; competidor regular 6000/kg (+20 %), promo 4000/kg (−20 %)
    assert r["diferencia_porcentual_regular"] == 20.0
    assert r["diferencia_porcentual_promo"] == -20.0
    assert r["base_comparacion"] == "precio por kg"


def test_moneda_distinta_sin_tipo_de_cambio_no_compara():
    prod = P("Gaseosa cola", "Coca-Cola", 1750, "ml")
    ev = evaluar_coincidencia(prod, "Gaseosa cola Coca Cola 1,75 lts", "Coca Cola")
    r = comparar(prod, {"precio_regular": 2, "moneda": "USD"}, ev)
    assert r["clasificacion"] == "sin_comparacion"


def test_parseo_promocion_vtex():
    comp = fuentes.COMPETIDORES["carrefour"]
    p = {"productName": "Lavandina Ayudín anti-splash 2 l.", "brand": "Ayudín", "link": "https://x/p",
         "items": [{"sellers": [{"commertialOffer": {
             "Price": 2999.75, "ListPrice": 4615.0, "PriceWithoutDiscount": 4615.0, "IsAvailable": True,
             "PromotionTeasers": [{"Name": "Tarjeta Carrefour 15%"}], "DiscountHighLight": []}}]}]}
    r = fuentes._parsear_producto_vtex(p, comp, "2026-11-15T10:00:00-03:00")
    assert r["precio_regular"] == 4615.0 and r["precio_promocional"] == 2999.75
    assert "35%" in r["promocion"] and r["promociones_condicionadas"] == ["Tarjeta Carrefour 15%"]
    assert r["url"] and r["fecha_consulta"]


def test_listprice_inflado_de_cencosud_se_ignora():
    comp = fuentes.COMPETIDORES["jumbo"]
    p = {"productName": "Gaseosa Cola Sabor Original 2.25 Lts Coca Cola", "brand": "Coca Cola", "link": "/x/p",
         "items": [{"sellers": [{"commertialOffer": {
             "Price": 6100.0, "ListPrice": 504132.0, "PriceWithoutDiscount": 6100.0, "IsAvailable": True}}]}]}
    r = fuentes._parsear_producto_vtex(p, comp, "2026-11-15T10:00:00-03:00")
    assert r["precio_regular"] == 6100.0 and r["precio_promocional"] is None
    assert r["url"].startswith("https://www.jumbo.com.ar/")


# --- Criterios 3, 8, 9 y 10: pipeline completo con fuente simulada ----------------------

@pytest.fixture
def db_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(historial, "DB_PATH", str(tmp_path / "precios.db"))
    from tools import informes
    monkeypatch.setattr(informes, "INFORMES_DIR", str(tmp_path / "informes"))
    monkeypatch.setattr("tools.tools.index_data", lambda *a, **k: "sin vector store en tests")
    return tmp_path


def _fake_busqueda(resultados_por_competidor):
    def buscar(clave, consulta, max_resultados=8):
        comp = fuentes.COMPETIDORES[clave]
        res = [{"competidor": comp["nombre"], "marca": m, "nombre_encontrado": n, "precio_regular": reg,
                "precio_promocional": promo, "promocion": "10% de descuento" if promo else None,
                "promociones_condicionadas": None, "disponible": True, "moneda": "ARS", "pais": "Argentina",
                "url": f"{comp['base']}/{clave}-item/p", "fuente": "test", "fecha_consulta": "2026-11-15T10:00:00-03:00"}
               for n, m, reg, promo in resultados_por_competidor.get(clave, [])]
        return {"competidor": comp["nombre"], "estado": "ok" if res else "sin_resultados", "resultados": res,
                "url_consulta": f"{comp['base']}/api?ft={consulta}", "fecha_consulta": "2026-11-15T10:00:00-03:00"}
    return buscar


def test_pipeline_end_to_end(db_temporal, monkeypatch):
    prod = obtener_producto("RAG-00488")  # Yerba mate Playadito 500 g
    monkeypatch.setattr(fuentes, "buscar_en_competidor", _fake_busqueda({
        "carrefour": [("Yerba mate Playadito con palo 500 g.", "Playadito", 3000.0, 2700.0),
                      ("Yerba mate Taragüi 500 g", "Taragüi", 2500.0, None)],
        "dia": [("Yerba mate Playadito sin palo 500 g.", "Playadito", 3100.0, None)],
        "jumbo": [],
    }))
    from tools.investigacion import investigar_productos
    r = investigar_productos("RAG-00488, RAG-77777", "Carrefour, Día, Jumbo, Coto")
    assert r["productos_inexistentes_en_catalogo"] == ["RAG-77777"]
    assert r["competidores_no_soportados"] == ["coto"]
    filas = {f["competidor"]: f for f in r["resultados"][0]["competidores"]}
    assert filas["Jumbo"]["estado"] == "no_encontrado"                    # no inventa precio
    assert filas["Día"]["estado"] == "requiere_revision"                   # variante "sin palo"
    assert filas["Carrefour"]["estado"] == "requiere_revision"             # variante "con palo"
    for f in filas.values():
        assert f.get("fecha_consulta")

    # Reutiliza lo guardado (F10) en una nueva consulta
    r2 = investigar_productos("RAG-00488", "Carrefour")
    assert r2["resultados"][0]["competidores"][0].get("reutilizado", "").startswith("si")

    from tools.informes import generar_informe
    inf = generar_informe(r["consulta_id"])
    assert os.path.exists(os.path.join(historial.RAIZ, inf["informe_markdown"])) or os.path.exists(inf["informe_markdown"])
    assert "Sin coincidencia confiable" in inf["contenido"] and "No encontrados" in inf["contenido"]
    assert prod["producto_id"] in inf["contenido"]


def test_pipeline_comparacion_directa(db_temporal, monkeypatch):
    prod = obtener_producto("RAG-00488")
    precio = float(prod["precio"])
    monkeypatch.setattr(fuentes, "buscar_en_competidor", _fake_busqueda({
        "carrefour": [("Yerba mate Playadito 500 g.", "Playadito", round(precio * 1.2, 2), round(precio * 0.9, 2))],
    }))
    from tools.investigacion import investigar_productos
    r = investigar_productos("RAG-00488", "Carrefour", forzar_actualizacion=True)
    f = r["resultados"][0]["competidores"][0]
    assert f["estado"] == "comparado" and f["confianza"] == "alto"
    assert f["diferencia_porcentual_regular"] == pytest.approx(20.0, abs=0.01)
    assert f["diferencia_porcentual_promo"] == pytest.approx(-10.0, abs=0.01)
    assert f["clasificacion"] == "ragnar_mas_barato_significativo"
    assert r["resumen"]["observaciones_comparables"] == 1
