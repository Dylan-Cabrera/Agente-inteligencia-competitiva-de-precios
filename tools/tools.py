from langchain_chroma import Chroma
import chromadb
import os
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

import csv
from langchain_core.documents import Document

CATALOGO_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "catalogo_ragnar.csv")
CATALOGO_COLLECTION = "catalogo_ragnar"

VECTOR_STORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vector_store")

def web_search(query: str, max_results: int = 5) -> list | str:
    """
    Busca información en la web utilizando DuckDuckGo.
    Args:
        query: str -> El término o pregunta a buscar en internet.
        max_results: int -> Número máximo de resultados a devolver.
    Returns:
        Lista de resultados de la búsqueda, o el mensaje de error.
    """
    try:
        # Se instancia DDGS correctamente
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return results
    except Exception as e:
        return str(e)

def index_data(text: str, c_name: str) -> list:
    """
    Divide un texto en fragmentos y los almacena en la base de datos vectorial.
    Args:
        text: str -> Texto que se desea indexar.
        c_name: str -> Nombre de la colección donde se guardarán los fragmentos.
    Returns:
        Lista con los IDs de los documentos agregados, o el mensaje de error.
    """
    try:
        vector_store= Chroma(
            collection_name= c_name,
            persist_directory=VECTOR_STORE_DIR,
            embedding_function=OllamaEmbeddings(
                model="nomic-embed-text"
            )
        )
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50
        )

        docs = splitter.create_documents([text])

        results =vector_store.add_documents(documents=docs)
        return results

    except Exception as e:
        return str(e)

def list_collections() -> list:
    """
    Lista los nombres de las colecciones existentes en la base de datos vectorial.
    Returns:
        Lista de nombres de colecciones, o el mensaje de error.
    """
    try:
        client= chromadb.PersistentClient(
            path=VECTOR_STORE_DIR
        )
        collections= client.list_collections()
        results = [getattr(collection, "name", collection) for collection in collections]
        return results
    except Exception as e:
        return str(e)

def retrieval(query:str, collection_name:str, k:int=5)-> list:
    """
    Herramienta para consultar la base de datos vectorial.
    Args:
        query: str -> Pregunta o texto a buscar.
        collection_name: str -> Nombre de la colección a consultar.
        k: int -> Número de resultados a devolver.
    Returns:
        Lista de tuplas (documento, puntaje de relevancia), o el mensaje de error.
    """

    try:
        vector_store= Chroma(
            collection_name= collection_name,
            persist_directory=VECTOR_STORE_DIR,
            embedding_function=OllamaEmbeddings(
                model="nomic-embed-text"
            )
        )

        return vector_store.similarity_search_with_relevance_scores(
            query=query,
            k=k
        )

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
        client = chromadb.PersistentClient(path=VECTOR_STORE_DIR)
        collection = client.get_collection(name=c_name)
        samples = collection.peek(limit=n_samples)
        return {
            "name": c_name,
            "count": collection.count(),
            "samples": samples["documents"],
        }
    except Exception as e:
        return str(e)


def index_csv(path: str, c_name: str) -> list | str:
    """
    Indexa un CSV en la base vectorial: una fila = un documento (sin dividir en fragmentos),
    con todas las columnas guardadas como metadata. Si se vuelve a indexar, actualiza las filas.
    Si el CSV tiene columna 'producto_id' se usa como id del documento.
    Args:
        path: str -> Ruta del archivo CSV.
        c_name: str -> Nombre de la colección donde se guardarán las filas.
    Returns:
        Lista con los IDs de los documentos agregados, o el mensaje de error.
    """
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return "El CSV no contiene filas."
        docs, ids = [], []
        for i, row in enumerate(rows):
            text = ", ".join(f"{k}: {v}" for k, v in row.items())
            docs.append(Document(page_content=text, metadata=dict(row)))
            ids.append(row.get("producto_id") or f"row-{i}")
        vector_store = Chroma(
            collection_name=c_name,
            persist_directory=VECTOR_STORE_DIR,
            embedding_function=OllamaEmbeddings(model="nomic-embed-text"),
        )
        return vector_store.add_documents(documents=docs, ids=ids)
    except Exception as e:
        return str(e)


def indexar_catalogo() -> str:
    """
    Carga catalogo_ragnar.csv en la colección 'catalogo_ragnar' (solo si todavía no está indexado).
    Returns:
        Mensaje con la cantidad de productos disponibles, o el mensaje de error.
    """
    try:
        client = chromadb.PersistentClient(path=VECTOR_STORE_DIR)
        names = [getattr(c, "name", c) for c in client.list_collections()]
        if CATALOGO_COLLECTION in names:
            n = client.get_collection(CATALOGO_COLLECTION).count()
            if n:
                return f"Catálogo ya indexado: {n} productos."
        res = index_csv(CATALOGO_CSV, CATALOGO_COLLECTION)
        return f"Catálogo indexado: {len(res)} productos." if isinstance(res, list) else res
    except Exception as e:
        return str(e)


def consultar_catalogo(query: str, k: int = 5) -> list | str:
    """
    Consulta SOLO LECTURA el catálogo de productos de Ragnar S.A. (búsqueda semántica).
    Cada resultado incluye producto_id, producto, marca, categoria, presentacion, contenido,
    unidad_medida, precio, moneda y pais.
    Args:
        query: str -> Producto, marca, categoría o producto_id a buscar (ej. "Leche entera La Serenísima").
        k: int -> Número máximo de resultados.
    Returns:
        Lista de productos (diccionarios) con su puntaje de relevancia, o el mensaje de error.
    """
    try:
        msg = indexar_catalogo()
        if not msg.startswith("Cat"):
            return msg
        vector_store = Chroma(
            collection_name=CATALOGO_COLLECTION,
            persist_directory=VECTOR_STORE_DIR,
            embedding_function=OllamaEmbeddings(model="nomic-embed-text"),
        )
        results = vector_store.similarity_search_with_relevance_scores(query=query, k=k)
        return [{**doc.metadata, "relevancia": round(score, 3)} for doc, score in results]
    except Exception as e:
        return str(e)
