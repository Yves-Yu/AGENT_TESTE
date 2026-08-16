"""
Ponto de entrada da aplicação.

Uso:
    python main.py

Ou via uvicorn diretamente (para desenvolvimento com reload):
    uvicorn app.web.app:app --reload --host 0.0.0.0 --port 8000
"""
import logging

from dotenv import load_dotenv

load_dotenv()  # carrega .env antes de tudo

from app.config import settings
from app.db import init_db

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    import uvicorn

    init_db()
    logging.getLogger(__name__).info(
        "Iniciando servidor em http://%s:%d", settings.host, settings.port
    )
    uvicorn.run(
        "app.web.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
