import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    ollama_url: str
    model: str
    data_dir: Path
    mcp_config: Path
    context_tokens: int = 131072
    compaction_tokens: int = 50000
    compaction_retention: int = 8

    def __post_init__(self):
        if self.context_tokens <= 0:
            raise ValueError("LOCALCHAT_CONTEXT_TOKENS must be positive")
        if self.compaction_tokens < 0:
            raise ValueError("LOCALCHAT_COMPACTION_TOKENS must be zero or positive")
        if self.compaction_retention < 0:
            raise ValueError("LOCALCHAT_COMPACTION_RETENTION must be zero or positive")


settings = Settings(
    ollama_url=os.getenv(
        "OLLAMA_URL",
        "http://127.0.0.1:11434",
    ).rstrip("/"),
    model=os.getenv("LOCALCHAT_MODEL", "gemma4:e2b"),
    data_dir=Path(os.getenv("LOCALCHAT_DATA", "data")),
    mcp_config=Path(os.getenv("LOCALCHAT_MCP_CONFIG", "mcp.toml")),
    context_tokens=int(os.getenv("LOCALCHAT_CONTEXT_TOKENS", "131072")),
    compaction_tokens=int(os.getenv("LOCALCHAT_COMPACTION_TOKENS", "50000")),
    compaction_retention=int(os.getenv("LOCALCHAT_COMPACTION_RETENTION", "8")),
)
