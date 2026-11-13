# TP Final – Agente de comparación de precios (Ragnar S.A.)

Base del proyecto reciclada de `~/clase_agentes` (Google ADK + Ollama/LiteLLM + ChromaDB).

- `agent/agent.py`: agente raíz con el system prompt del problema (ver `todo.md`, sección 1).
- `agent/subagents/subagents_01.py`: subagente que guarda informes en `informes/`.
- `tools/tools.py`: `web_search` (DuckDuckGo), herramientas de base vectorial, `index_csv`
  (nuevo, lee CSV) y `consultar_catalogo` (solo lectura sobre `catalogo_ragnar.csv`, se indexa solo la primera vez).

## Ejecutar
```bash
python -m venv env && source env/bin/activate   # fish: activate.fish
pip install -r requirements.txt
ollama pull nomic-embed-text && ollama pull qwen3:8b
adk web    # o: adk run agent
```
Pendiente (ver `todo.md`): matching de productos, cálculo de diferencias, clasificación, promociones, informes, tests.
