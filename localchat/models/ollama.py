import httpx

from localchat.config import settings


async def fetch_models():
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{settings.ollama_url}/api/tags"
        )
        response.raise_for_status()
        return response.json()


async def fetch_model_details(model: str):
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{settings.ollama_url}/api/show",
            json={"model": model},
        )
        response.raise_for_status()
        return response.json()
