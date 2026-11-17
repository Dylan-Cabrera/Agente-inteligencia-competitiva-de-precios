"""
Pipeline de investigación (F02 -> F03 -> F04 -> F05/F06 -> F07 -> F09 -> F10).
Consigna 3 ("realizar estas tareas de manera coordinada ... sin que los empleados deban
ejecutar manualmente cada búsqueda") / criterios de aceptación 2 a 9.

Por qué una tool "de alto nivel": el modelo local (qwen3:4b) es chico. Si tiene que encadenar
catálogo -> búsqueda -> matching -> cálculo -> guardado con 5 llamadas por producto y
competidor, se equivoca, saltea pasos o calcula mal. Este pipeline hace los pasos
determinísticos con código y el LLM se encarga de lo que hace bien: entender la solicitud,
elegir productos/competidores y redactar conclusiones.
"""
from __future__ import annotations

from tools import descubrimiento, fuentes, historial
from tools.catalogo import obtener_producto
from tools.comparacion import comparar, resumen_posicionamiento
from tools.matching import SINONIMOS_TIPO, evaluar_coincidencia, puntaje, tokens

MAX_PRODUCTOS = 15


def _consultas_busqueda(producto: dict) -> list[str]:
    nombre, marca = producto["producto"], producto["marca"]
    primeras = " ".join(tokens(nombre)[:1])
    consultas = [f"{nombre} {marca}", f"{marca} {primeras}"]
    # Nombre regional del producto (ej. "lavandina" se busca como "cloro" en México).
    for t in tokens(nombre)[:1]:
        consultas += [f"{alt} {marca}" for alt in SINONIMOS_TIPO.get(t, [])[1:3]]
    return list(dict.fromkeys(consultas))


def _mejor_candidato(producto: dict, resultados: list[dict]) -> tuple[dict | None, dict | None, list]:
    evaluados = []
    for r in resultados:
        ev = evaluar_coincidencia(producto, r["nombre_encontrado"], r.get("marca", ""))
        # Desempate: disponible y, si cambia la presentación, la más cercana a la de Ragnar.
        c = ev.get("contenido") or {}
        cercania = 0.0
        if c.get("ragnar") and c.get("encontrado") and c["ragnar"]["cantidad"]:
            cercania = min(abs(c["encontrado"]["cantidad"] - c["ragnar"]["cantidad"]) / c["ragnar"]["cantidad"], 1)
        evaluados.append((puntaje(ev) + (0.2 if r.get("disponible") else 0) - 0.5 * cercania, r, ev))
    if not evaluados:
        return None, None, []
    evaluados.sort(key=lambda x: -x[0])
    _, r, ev = evaluados[0]
    alternativas = [{"nombre": x[1]["nombre_encontrado"], "confianza": x[2]["confianza"],
                     "motivo": x[2]["motivos"][0] if x[2]["motivos"] else ""} for x in evaluados[1:4]]
    return r, ev, alternativas


def investigar_producto_en_competidor(producto: dict, clave: str, consulta_id: str,
                                      tipo_cambio: float | None = None, nota_cambio: str = "") -> dict:
    comp = fuentes.COMPETIDORES[clave]
    base = {k: producto.get(k) for k in ("producto_id", "producto", "marca", "categoria",
                                         "presentacion", "moneda", "pais")}
    base.update(consulta_id=consulta_id, precio_ragnar=float(producto["precio"]), competidor=comp["nombre"])

    if comp.get("estado") == "bloqueado":
        return {**base, "estado": "acceso_no_permitido", "fecha_consulta": fuentes.ahora_iso(),
                "motivos": [f"{comp['nombre']} bloquea el acceso automatizado ({comp.get('motivo', '')}); "
                            "no se evade el bloqueo"]}
    if comp["moneda"] != producto.get("moneda") and not tipo_cambio:
        # Otra región/moneda: solo se compara si el usuario informa un tipo de cambio explícito.
        return {**base, "estado": "fuera_de_region", "fecha_consulta": fuentes.ahora_iso(),
                "motivos": [f"{comp['nombre']} opera en {comp['pais']} ({comp['moneda']}); el producto se vende en "
                            f"{producto.get('pais')} ({producto.get('moneda')}). No se pudo obtener el tipo de cambio "
                            f"{comp['moneda']}→{producto.get('moneda')}: {nota_cambio or 'sin dato'}. "
                            "Se puede informar uno manualmente."]}

    resultados, ultima, estado_fuente = [], None, None
    mejor = ev = None
    alternativas: list = []
    for consulta in _consultas_busqueda(producto):
        ultima = fuentes.buscar_en_competidor(clave, consulta)
        estado_fuente = ultima["estado"]
        if estado_fuente in ("error_fuente", "acceso_no_permitido"):
            break
        resultados += ultima["resultados"]
        mejor, ev, alternativas = _mejor_candidato(producto, resultados)
        if ev and ev["comparable"] != "no":
            break

    base.update(fecha_consulta=ultima["fecha_consulta"], url_consulta=ultima["url_consulta"])
    if estado_fuente in ("error_fuente", "acceso_no_permitido") and not resultados:
        return {**base, "estado": estado_fuente, "motivos": [ultima.get("detalle", "")]}
    if not mejor:
        return {**base, "estado": "no_encontrado",
                "motivos": [f"{comp['nombre']} no devolvió resultados para el producto"]}

    obs = {**base,
           "nombre_encontrado": mejor["nombre_encontrado"], "marca_encontrada": mejor.get("marca"),
           "precio_regular": mejor["precio_regular"], "precio_promocional": mejor["precio_promocional"],
           "promocion": mejor["promocion"], "promociones_condicionadas": mejor["promociones_condicionadas"],
           "disponible": mejor["disponible"], "url": mejor["url"], "fuente": mejor["fuente"],
           "fecha_consulta": mejor["fecha_consulta"],
           "confianza": ev["confianza"], "comparable": ev["comparable"], "motivos": ev["motivos"]}

    if ev["confianza"] == "sin_coincidencia":
        obs["estado"] = "sin_coincidencia_confiable"
        obs["clasificacion"] = "sin_coincidencia_confiable"
        obs["detalle"] = {"alternativas_descartadas": alternativas}
        return obs

    comp_res = comparar(producto, mejor, ev, tipo_cambio)
    if comp["moneda"] != producto.get("moneda") and tipo_cambio:
        obs["motivos"] = obs["motivos"] + [
            f"precio convertido de {comp['moneda']} a {producto.get('moneda')}: {nota_cambio or tipo_cambio}",
            f"comparación entre países ({comp['pais']} vs {producto.get('pais')}): indicativa, "
            "no contempla impuestos, costos logísticos ni poder adquisitivo locales"]
    obs["estado"] = "comparado" if ev["comparable"] != "no" else "requiere_revision"
    if ev["comparable"] == "no":
        obs["clasificacion"] = "sin_coincidencia_confiable"
    else:
        nr, nc = comp_res.get("precio_normalizado_ragnar"), comp_res.get("precio_normalizado_competidor")
        obs.update({k: comp_res.get(k) for k in (
            "base_comparacion", "diferencia_absoluta_regular", "diferencia_porcentual_regular",
            "diferencia_absoluta_promo", "diferencia_porcentual_promo", "clasificacion",
            "clasificacion_promo")})
        obs.update(precio_normalizado_ragnar=nr and nr["precio"], precio_normalizado_competidor=nc and nc["precio"],
                   unidad_normalizacion=nr and nr["por"])
        if comp_res.get("motivo"):
            obs["motivos"] = obs["motivos"] + [comp_res["motivo"]]
    if alternativas:
        obs["detalle"] = {"otras_opciones": alternativas}
    return obs


_CAMPOS_RESPUESTA = ["competidor", "estado", "nombre_encontrado", "precio_regular", "precio_promocional",
                     "promocion", "promociones_condicionadas", "confianza", "motivos", "base_comparacion",
                     "diferencia_absoluta_regular", "diferencia_porcentual_regular",
                     "diferencia_porcentual_promo", "clasificacion", "url", "fecha_consulta", "reutilizado"]


def investigar_productos(producto_ids: str, competidores: str = "", solicitud: str = "",
                         forzar_actualizacion: bool = False, tipo_cambio: float = 0.0) -> dict:
    """
    Investiga precios de productos del catálogo de Ragnar en supermercados competidores, de punta
    a punta: busca en cada competidor, verifica que sea el MISMO producto (marca, tipo, variante,
    contenido neto, unidad), separa precio regular y promocional, calcula diferencias absolutas
    y porcentuales (o por unidad de medida si cambia la presentación), clasifica el resultado y
    guarda todo con fuente (URL) y fecha de consulta. Reutiliza precios consultados hace menos de
    24 h salvo forzar_actualizacion=True.
    Args:
        producto_ids: str -> Ids del catálogo separados por coma (ej. "RAG-00001,RAG-00042"). Máximo 15.
        competidores: str -> Ej. "Carrefour, Chedraui". Vacío = Carrefour, Jumbo y Día. "todos" = todos.
                             Si se nombra uno que no está registrado, se lo busca en la web y se registra.
        solicitud: str -> Texto de la solicitud del usuario (para el historial).
        forzar_actualizacion: bool -> True para ignorar precios guardados recientes.
        tipo_cambio: float -> Opcional. Si el competidor cotiza en otra moneda se usa la cotización de
                              mercado automáticamente; pasar un valor solo si el usuario dio uno
                              (unidades de moneda de Ragnar por 1 unidad de la del competidor).
    Returns:
        Diccionario con consulta_id, resultados por producto y competidor, y resumen de posicionamiento.
    """
    try:
        ids = [i.strip() for i in producto_ids.replace(";", ",").split(",") if i.strip()]
        if not ids:
            return {"error": "Indicar al menos un producto_id (usar consultar_catalogo para obtenerlo)."}
        aviso = None
        if len(ids) > MAX_PRODUCTOS:
            aviso = f"Se investigan los primeros {MAX_PRODUCTOS} de {len(ids)} productos."
            ids = ids[:MAX_PRODUCTOS]
        productos, inexistentes = [], []
        for i in ids:
            p = obtener_producto(i)
            (productos.append(p) if p else inexistentes.append(i))
        claves, no_soportados = fuentes.resolver_competidores(competidores)
        descubiertos, no_disponibles = [], []
        for nombre in no_soportados:  # supermercado nuevo: buscarlo en la web y registrarlo
            d = descubrimiento.descubrir_competidor(nombre)
            if d.get("estado") == "registrado" or (d.get("estado") == "ya_registrado"
                                                     and d.get("estado_sitio") != "bloqueado"):
                claves.append(d["clave"])
                descubiertos.append(f"{d['nombre']} ({d['pais']}, {d['tipo']})")
            elif d.get("estado") == "bloqueado":
                claves.append(d["clave"])  # se informa como acceso_no_permitido en los resultados
            else:
                no_disponibles.append({"competidor": nombre, "estado": d.get("estado"),
                                       "motivo": d.get("motivo", "")})
        no_soportados = no_disponibles
        if not claves:
            return {"error": "Ningún competidor válido.", "competidores_no_soportados": no_soportados,
                    "disponibles": [c["nombre"] for c in fuentes.listar_competidores()]}

        consulta_id = historial.nueva_consulta(solicitud or f"investigar {producto_ids}",
                                               [p["producto_id"] for p in productos],
                                               [fuentes.COMPETIDORES[c]["nombre"] for c in claves])
        salida, todas = [], []
        cambios: dict[tuple[str, str], tuple[float | None, str]] = {}

        def cambio_para(moneda_comp: str, moneda_rag: str) -> tuple[float | None, str]:
            if moneda_comp == moneda_rag:
                return None, ""
            if tipo_cambio:  # informado por el usuario: tiene prioridad
                return tipo_cambio, f"1 {moneda_comp} = {tipo_cambio} {moneda_rag} (informado por el usuario)"
            if (moneda_comp, moneda_rag) not in cambios:
                fx = descubrimiento.obtener_tipo_cambio(moneda_comp, moneda_rag)
                cambios[(moneda_comp, moneda_rag)] = (
                    (fx["tipo_cambio"], f"{fx['descripcion']} (fuente: {fx['fuente']}, actualizado {fx['actualizado']})")
                    if "tipo_cambio" in fx else (None, fx.get("error", "")))
            return cambios[(moneda_comp, moneda_rag)]

        for p in productos:
            filas = []
            for c in claves:
                previa = None if forzar_actualizacion else historial.observacion_reciente(
                    p["producto_id"], fuentes.COMPETIDORES[c]["nombre"])
                if previa:
                    obs = {k: previa.get(k) for k in historial.COLUMNAS}
                    obs.update(consulta_id=consulta_id, reutilizado=f"si (consultado {previa['fecha_consulta']})")
                else:
                    tc, nota = cambio_para(fuentes.COMPETIDORES[c]["moneda"], p["moneda"])
                    obs = investigar_producto_en_competidor(p, c, consulta_id, tc, nota)
                historial.guardar_observacion(obs)
                todas.append(obs)
                filas.append({k: obs.get(k) for k in _CAMPOS_RESPUESTA if obs.get(k) not in (None, "", [])})
            salida.append({"producto_id": p["producto_id"],
                           "producto": f"{p['producto']} {p['marca']} {p['presentacion']}",
                           "categoria": p["categoria"],
                           "precio_ragnar": float(p["precio"]), "moneda": p["moneda"],
                           "competidores": filas})

        out = {"consulta_id": consulta_id, "resultados": salida,
               "resumen": resumen_posicionamiento(todas),
               "convencion": "diferencia = precio competidor - precio Ragnar; % sobre precio Ragnar. "
                             "Negativo => Ragnar más caro."}
        if inexistentes:
            out["productos_inexistentes_en_catalogo"] = inexistentes
        if no_soportados:
            out["competidores_no_disponibles"] = no_soportados
        if descubiertos:
            out["competidores_descubiertos"] = descubiertos
        if cambios:
            out["tipos_de_cambio"] = [n for _, n in cambios.values()]
        if aviso:
            out["aviso"] = aviso
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
