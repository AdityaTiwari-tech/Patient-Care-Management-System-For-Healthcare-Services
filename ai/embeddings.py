"""
ai/embeddings.py
Wraps sentence-transformers for local embeddings used by the Chroma
knowledge base. Falls back to a simple bag-of-words vector if the
`sentence-transformers` package isn't installed, so the app still runs.
"""
from functools import lru_cache

_MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _get_model():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(_MODEL_NAME)
    except Exception:
        return None


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    if model is not None:
        return model.encode(texts, convert_to_numpy=True).tolist()

    # Fallback: crude hashed bag-of-words vector (keeps the app functional
    # without the sentence-transformers download).
    import hashlib
    dim = 128
    vectors = []
    for t in texts:
        vec = [0.0] * dim
        for word in t.lower().split():
            idx = int(hashlib.md5(word.encode()).hexdigest(), 16) % dim
            vec[idx] += 1.0
        vectors.append(vec)
    return vectors
