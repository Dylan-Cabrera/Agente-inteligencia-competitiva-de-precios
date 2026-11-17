"""Modelo del agente. Por defecto GPT (OpenAI) vía LiteLLM; la clave va en agent/.env como OPENAI_API_KEY.
Otras opciones con MODELO_AGENTE:
  gemini-3.1-flash-lite                          (GOOGLE_API_KEY en agent/.env)
  ollama_chat/qwen3:4b-instruct-2507-q4_K_M      (local, lento en CPU)"""
import os

MODELO = os.environ.get("MODELO_AGENTE", "openai/gpt-5.4-mini")


def crear_modelo():
    if MODELO.startswith("gemini"):
        return MODELO  # ADK usa Gemini directamente con GOOGLE_API_KEY
    from google.adk.models.lite_llm import LiteLlm
    if MODELO.startswith("ollama"):
        os.environ.setdefault("OLLAMA_API_BASE", "http://localhost:11434")
        # Ollama usa 4096 tokens de contexto por defecto y el prompt + herramientas ya ocupan ~3500.
        return LiteLlm(model=MODELO, num_ctx=int(os.environ.get("NUM_CTX", "16384")),
                       reasoning_effort=os.environ.get("REASONING_EFFORT", "low" if "gpt-oss" in MODELO else "none"))
    return LiteLlm(model=MODELO)  # openai/... usa OPENAI_API_KEY
