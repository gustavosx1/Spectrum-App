# embedding para clusterização dos artigos em acontecimentos (tópicos)

import httpx


async def generate_embedding(text: str, api_key: str):
    resp = await httpx.AsyncClient().post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "text-embedding-004:embedContent",
        headers={"Content-Type": "application/json"},
        params={"key": api_key},
        json={
            "model": "models/text-embedding-004",
            "content": {"parts": [{"text": text}]},
        },
    )
    return resp.json()["embedding"]["values"]
