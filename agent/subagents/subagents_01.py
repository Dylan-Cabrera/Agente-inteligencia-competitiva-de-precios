from google.adk.agents.llm_agent import Agent
from agent.modelo import crear_modelo
from google.adk.tools.environment import EnvironmentToolset
from google.adk.environment import LocalEnvironment
import os

WORKING_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "informes")
os.makedirs(WORKING_DIR, exist_ok=True)

agent_01 = Agent(
    model=crear_modelo(),
    name='agent_01',
    description='Guarda informes comparativos (Markdown/CSV) y lee o modifica archivos en la carpeta de trabajo informes.',
    instruction="""Eres un agente que gestiona archivos dentro de tu carpeta de trabajo (informes).

    Puedes crear, leer, modificar y borrar archivos usando tus herramientas.

    Reglas:
    - Trabaja solo dentro de tu carpeta de trabajo, nunca fuera de ella.
    - Usa rutas relativas a tu carpeta de trabajo.
    - Antes de modificar o borrar un archivo, verifica que existe.
    - No sobrescribas un archivo existente sin que el usuario lo haya pedido.
    - Al terminar, informa en español qué archivo creaste, modificaste o borraste.
    """,
    tools=[EnvironmentToolset(
        environment=LocalEnvironment(
            working_dir=WORKING_DIR
        )
    )],
)