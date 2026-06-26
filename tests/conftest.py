"""
Configuração compartilhada dos testes.

O módulo bot.py lê variáveis de ambiente em tempo de import e converte
algumas para int (API_ID, MEU_CANAL_ID, AWIN_KABUM_ADVERTISER_ID).
Por isso precisamos popular o ambiente ANTES de qualquer 'import bot'.
"""
import os
import sys

# --- credenciais fictícias só para o import não quebrar ---
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "fake_api_hash")
os.environ.setdefault("BOT_TOKEN", "fake_bot_token")
os.environ.setdefault("MEU_CANAL_ID", "-1001234567890")
os.environ.setdefault("ALIEXPRESS_APP_KEY", "ali_key")
os.environ.setdefault("ALIEXPRESS_APP_SECRET", "ali_secret")
os.environ.setdefault("SHOPEE_APP_ID", "shopee_id")
os.environ.setdefault("SHOPEE_SECRET", "shopee_secret")
os.environ.setdefault("AMAZON_ASSOCIATE_TAG", "meutag-20")
os.environ.setdefault("AWIN_PUBLISHER_ID", "999999")
os.environ.setdefault("AWIN_ACCESS_TOKEN", "awin_token")
os.environ.setdefault("AWIN_KABUM_ADVERTISER_ID", "17729")
os.environ.setdefault("MELI_COOKIE", "fake_cookie")
os.environ.setdefault("MELI_X_CSRF_TOKEN", "fake_csrf")
os.environ.setdefault("MELI_AFFILIATE_TAG", "ta20250609093813")
os.environ.setdefault("TELEGRAM_ADMIN_ID", "111")

# garante que 'import bot' encontre o módulo na pasta acima de tests/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import observable as botmod


@pytest.fixture(autouse=True)
def limpar_cache():
    """Cada teste começa com o cache de deduplicação vazio."""
    botmod.cache_links.clear()
    yield
    botmod.cache_links.clear()


class FakeResponse:
    """Resposta HTTP falsa para substituir requests.get / requests.post."""
    def __init__(self, *, url="", text="", status_code=200, json_data=None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data


@pytest.fixture
def fake_response():
    return FakeResponse
