"""
Herramientas genéricas: búsqueda web y base vectorial (ChromaDB + embeddings de Ollama).

Las dependencias pesadas (langchain, chromadb) se importan dentro de cada función para que
el resto de las tools (catálogo, matching, comparación, informes) funcione y se pueda testear
aunque Ollama no esté levantado.
"""
import csv
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGO_CSV = os.path.join(RAIZ, "catalogo_ragnar.csv")
CATALOGO_COLLECTION = "catalogo_ragnar_v2"  # v2: textos con prefijos de tarea de nomic
INFORMES_COLLECTION = "informes"
VECTOR_STORE_DIR = os.path.join(RAIZ, "vector_store")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")


def _vector_store(c_name: str):
    from langchain_chroma import Chroma
    from langchain_ollama import OllamaEmbeddings
    return Chroma(collection_name=c_name, persist_directory=VECTOR_STORE_DIR,
                  embedding_function=OllamaEmbeddings(model=EMBEDDING_MODEL))


def _client():
    import chromadb
    return chromadb.PersistentClient(path=VECTOR_STORE_DIR)


def web_search(query: str, max_results: int = 5) -> list | str:
    """
    Busca información general en la web (DuckDuckGo). Útil para contexto (ej. si un competidor
    sin fuente estructurada vende un producto). NO usar sus fragmentos como precio para comparar:
    los precios comparables salen de investigar_productos / buscar_precio_competencia.
    Args:
        query: str -> El término o pregunta a buscar en internet.
        max_results: int -> Número máximo de resultados a devolver.
    Returns:
        Lista de resultados (title, href, body), o el mensaje de error.
    """
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"Error en la búsqueda web: {e}"


def index_data(text: str, c_name: str, metadata: dict | None = None) -> list | str:
    """
    Divide un texto en fragmentos y los almacena en la base de datos vectorial.
    Args:
        text: str -> Texto que se desea indexar.
        c_name: str -> Nombre de la colección donde se guardarán los fragmentos.
    Returns:
        Lista con los IDs de los documentos agregados, o el mensaje de error.
    """
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        docs = splitter.create_documents([text], metadatas=[metadata or {}])
        return _vector_store(c_name).add_documents(documents=docs)
    except Exception as e:
        return str(e)


def list_collections() -> list | str:
    """
    Lista los nombres de las colecciones existentes en la base de datos vectorial.
    Returns:
        Lista de nombres de colecciones, o el mensaje de error.
    """
    try:
        return [getattr(c, "name", c) for c in _client().list_collections()]
    except Exception as e:
        return str(e)


def retrieval(query: str, collection_name: str, k: int = 5) -> list | str:
    """
    Consulta la base de datos vectorial por similitud.
    Args:
        query: str -> Pregunta o texto a buscar.
        collection_name: str -> Nombre de la colección a consultar.
        k: int -> Número de resultados a devolver.
    Returns:
        Lista de {contenido, metadata, relevancia}, o el mensaje de error.
    """
    try:
        res = _vector_store(collection_name).similarity_search_with_relevance_scores(query=query, k=k)
        return [{"contenido": d.page_content, "metadata": d.metadata, "relevancia": round(s, 3)}
                for d, s in res]
    except Exception as e:
        return str(e)


def get_collection_info(c_name: str, n_samples: int = 3) -> dict | str:
    """
    Devuelve información de una colección: cuántos documentos tiene y algunos ejemplos.
    Args:
        c_name: str -> Nombre de la colección a inspeccionar.
        n_samples: int -> Número de fragmentos de ejemplo a mostrar.
    Returns:
        Diccionario con el nombre, el total de documentos y los ejemplos, o el mensaje de error.
    """
    try:
        collection = _client().get_collection(name=c_name)
        return {"name": c_name, "count": collection.count(),
                "samples": collection.peek(limit=n_samples)["documents"]}
    except Exception as e:
        return str(e)


def index_csv(path: str, c_name: str) -> list | str:
    """
    Indexa un CSV en la base vectorial: una fila = un documento (sin dividir en fragmentos),
    con todas las columnas guardadas como metadata. Si el CSV tiene columna 'producto_id' se
    usa como id del documento (re-indexar actualiza en lugar de duplicar).
    Args:
        path: str -> Ruta del archivo CSV.
        c_name: str -> Nombre de la colección donde se guardarán las filas.
    Returns:
        Lista con los IDs de los documentos agregados, o el mensaje de error.
    """
    try:
        from langchain_core.documents import Document
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return "El CSV no contiene filas."
        docs, ids = [], []
        for i, row in enumerate(rows):
            # Texto pensado para el embedding: lo semántico primero (qué es), luego los atributos.
            # nomic-embed-text está entrenado con prefijos de tarea: "search_document:" al indexar
            # y "search_query:" al consultar. Sin ellos la recuperación empeora notablemente.
            text = (f"search_document: {row.get('producto', '')} {row.get('marca', '')} {row.get('presentacion', '')}. "
                    f"Categoría: {row.get('categoria', '')}. "
                    + ", ".join(f"{k}: {v}" for k, v in row.items()))
            docs.append(Document(page_content=text, metadata=dict(row)))
            ids.append(row.get("producto_id") or f"row-{i}")
        return _vector_store(c_name).add_documents(documents=docs, ids=ids)
    except Exception as e:
        return str(e)


def indexar_catalogo() -> str:
    """Carga catalogo_ragnar.csv en la colección 'catalogo_ragnar' (solo la primera vez)."""
    try:
        if CATALOGO_COLLECTION in list_collections():
            n = _client().get_collection(CATALOGO_COLLECTION).count()
            if n:
                return f"Catálogo ya indexado: {n} productos."
        res = index_csv(CATALOGO_CSV, CATALOGO_COLLECTION)
        if isinstance(res, list):
            return f"Catálogo indexado: {len(res)} productos."
        raise RuntimeError(res)
    except Exception as e:
        raise RuntimeError(f"No se pudo indexar el catálogo: {e}") from e


def busqueda_semantica_catalogo(query: str, k: int = 30) -> list[tuple[str, float]]:
    """Ids de producto ordenados por similitud semántica (lanza excepción si Ollama no está)."""
    indexar_catalogo()
    res = _vector_store(CATALOGO_COLLECTION).similarity_search_with_relevance_scores(
        query=f"search_query: {query}", k=k)
    return [(d.metadata.get("producto_id"), s) for d, s in res]
