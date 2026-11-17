# TP Final – Agente de inteligencia competitiva de precios (Ragnar S.A.)

Agente de IA que recibe solicitudes en lenguaje natural del área comercial, consulta el catálogo
de Ragnar, busca los mismos productos en supermercados competidores, **verifica que sean el mismo
artículo**, compara precios (regular y promocional, absolutos, porcentuales y por unidad de
medida) y genera informes con **fuente (URL) y fecha de consulta** de cada precio.

Stack: Google ADK + LiteLLM + Ollama (`qwen3:4b-instruct-2507`; alternativa más precisa y lenta: `gpt-oss:20b`), ChromaDB + `nomic-embed-text` (RAG),
SQLite (historial de precios), APIs públicas VTEX de los supermercados.

---

## 1. Ejecutar

```bash
python -m venv env && source env/bin/activate        # fish: source env/bin/activate.fish
pip install -r requirements.txt
ollama serve &                                        # si no corre como servicio
ollama pull nomic-embed-text && ollama pull qwen3:4b-instruct-2507-q4_K_M
adk web                                               # o: adk run agent
# historial de chat persistente entre reinicios (F10):
adk web --session_service_uri sqlite://data/sesiones.db
```

Tests (no requieren red ni Ollama):

```bash
python -m pytest -q        # 23 tests
```

Variables de entorno opcionales:

| Variable | Default | Uso |
|---|---|---|
| `MODELO_AGENTE` | `ollama_chat/qwen3:4b-instruct-2507-q4_K_M` | Modelo LiteLLM del agente y subagente |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Embeddings para ChromaDB |
| `UMBRAL_DIFERENCIA_SIGNIFICATIVA` | `10` | % a partir del cual una diferencia es "significativa" |
| `MAX_ANTIGUEDAD_HORAS` | `24` | Antigüedad máxima para reutilizar un precio guardado |
| `INTERVALO_MIN_SEG` | `1.0` | Segundos mínimos entre requests al mismo sitio |
| `PRECIOS_DB` | `data/precios.db` | Base SQLite del historial |
| `CATALOGO_CSV` | `catalogo_ragnar.csv` | Archivo CSV del catálogo a usar (relativo a la raíz del proyecto) |

Competidores: viven en `competidores.json` (no en el código). Si el usuario nombra un supermercado que no
está, el agente usa `descubrir_competidor`: lo busca en la web, verifica `robots.txt`, detecta si se consulta por
API VTEX o por scraping (`tools/scraper_web.py`: JSON-LD / `__NEXT_DATA__`) y lo guarda para la próxima vez.
Los sitios que rechazan clientes automatizados (Coto, La Anónima, Libertad, Soriana) quedan registrados como
`bloqueado` y se informan, sin evadir el bloqueo. Con competidores de otro país el precio se convierte con la
cotización de mercado (`obtener_tipo_cambio`, open.er-api.com) y se informa fuente y fecha del tipo de cambio.

### Ejemplos de solicitudes

- *"¿Cómo estamos con la lavandina Ayudín de 1 litro frente a Carrefour y Día?"*
- *"Compará las gaseosas Coca-Cola contra Jumbo"* → luego *"ahora agregá Vea"* (F10)
- *"Armame un informe de la última investigación"*
- *"¿Qué habíamos visto del arroz Gallo?"* (RAG sobre informes anteriores)
- *"Bajá el precio de la yerba a 2000"* → el agente lo rechaza (catálogo de solo lectura)

Un informe real generado por el sistema está en
[`docs/ejemplos/informe_ejemplo.md`](docs/ejemplos/informe_ejemplo.md) (+ `.csv`).

---

## 2. Arquitectura

```
Empleado (lenguaje natural)
        │
        ▼
root_agent (qwen3:4b) ── F01 interpreta productos / competidores / región, pide aclaraciones
   │
   ├─ consultar_catalogo ........ F02  RAG híbrido: id exacto + filtros + léxico + semántico (RRF)
   ├─ investigar_productos ...... pipeline determinístico por producto × competidor:
   │     ├─ fuentes.py ........... F03  API pública VTEX (robots.txt, rate limit, reintentos)
   │     ├─ matching.py .......... F04  ¿es el mismo producto? → alto / medio / sin_coincidencia
   │     ├─ comparacion.py ....... F05/F06/F07  diferencias, normalización, promos, clasificación
   │     └─ historial.py ......... F09/F10  SQLite con URL + fecha, reutiliza precios < 24 h
   ├─ generar_informe ........... F08/F09  Markdown + CSV en informes/, indexado en ChromaDB
   ├─ consultar_historial / listar_consultas / buscar_en_informes ... F10
   ├─ buscar_precio_competencia / listar_competidores / web_search ... exploración
   └─ agent_01 (subagente) ...... gestión de archivos dentro de informes/
```

| Archivo | Contenido |
|---|---|
| `agent/agent.py` | Agente raíz y system prompt |
| `agent/subagents/subagents_01.py` | Subagente de archivos (sin cambios funcionales) |
| `tools/catalogo.py` | Consulta híbrida del catálogo (solo lectura) |
| `tools/fuentes.py` | Registro de competidores y cliente HTTP a VTEX |
| `tools/matching.py` | Reglas de coincidencia de productos |
| `tools/comparacion.py` | Cálculos de precios y clasificación |
| `tools/investigacion.py` | Pipeline `investigar_productos` |
| `tools/historial.py` | Persistencia SQLite |
| `tools/informes.py` | Informes y búsqueda en informes previos |
| `tools/tools.py` | Búsqueda web y utilidades de ChromaDB |
| `tests/test_agente.py` | Tests de los criterios de aceptación |

