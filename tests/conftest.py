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
# Os testes antigos não podem chamar a Bot API por causa do termômetro de
# preço; os que testam os modos trocam observable.MODO_COMENTARIO_PRECO.
os.environ.setdefault("COMENTARIO_PRECO", "sombra")

# garante que 'import bot' encontre o módulo na pasta acima de tests/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import historico_precos
import observable as botmod


@pytest.fixture(autouse=True)
def limpar_cache():
    """Cada teste começa com o cache de deduplicação vazio e sem cooldown
    pendente do alerta de expiração do Mercado Livre."""
    botmod.cache_links.clear()
    botmod._ultimo_alerta_expiracao = 0.0
    yield
    botmod.cache_links.clear()
    botmod._ultimo_alerta_expiracao = 0.0


@pytest.fixture(autouse=True)
def estado_isolado(tmp_path, monkeypatch):
    """O estado persistente (estado_bot.json) vai para uma pasta temporária —
    nunca para o diretório do projeto — e o estado em memória começa vazio."""
    import asyncio
    monkeypatch.setattr(botmod, "CAMINHO_ESTADO", str(tmp_path / "estado_bot.json"))
    monkeypatch.setattr(botmod, "_ultimo_processado", None)
    monkeypatch.setattr(botmod, "_trava_processamento", asyncio.Lock())
    botmod._ultimo_id_visto.clear()
    yield
    botmod._ultimo_id_visto.clear()


@pytest.fixture(autouse=True)
def historico_isolado(tmp_path, monkeypatch):
    """O histórico de preços vai para uma pasta temporária e o estado dos
    comentários (suspensão, limite por hora, cache de redirects) começa zerado."""
    monkeypatch.setattr(historico_precos, "CAMINHO_BANCO", str(tmp_path / "historico_precos.db"))
    monkeypatch.setattr(botmod, "_entidade_canal", None)
    monkeypatch.setattr(botmod, "_comentarios_suspensos_ate", 0.0)
    botmod._comentarios_recentes.clear()
    botmod.cache_destinos.clear()
    yield
    botmod._comentarios_recentes.clear()
    botmod.cache_destinos.clear()


class FakeResponse:
    """Resposta HTTP falsa para substituir requests.get / requests.post."""
    def __init__(self, *, url="", text="", status_code=200, json_data=None, headers=None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data

    def close(self):
        pass


@pytest.fixture
def fake_response():
    return FakeResponse
