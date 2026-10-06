"""
Testes de regressão — conversores de link (com requests mockado) e a
lógica de roteamento / substituição que decide se a mensagem é enviada.

Nenhuma chamada de rede real é feita: bot.requests.get/post são
substituídos por fakes via monkeypatch.
"""
import observable
import pytest


# ----------------------------------------------------------------------
# converter_link_amazon (URL direta não chama rede)
# ----------------------------------------------------------------------
class TestConverterAmazon:
    def test_adiciona_tag_de_afiliado(self):
        out = observable.converter_link_amazon("https://www.amazon.com.br/dp/B0ABC")
        assert "tag=meutag-20" in out

    def test_substitui_tag_de_terceiro(self):
        out = observable.converter_link_amazon("https://www.amazon.com.br/dp/B0?tag=outro-21")
        assert "tag=meutag-20" in out
        assert "outro-21" not in out

    def test_remove_linkcode_e_linkid(self):
        out = observable.converter_link_amazon(
            "https://www.amazon.com.br/dp/B0?linkCode=xx&linkId=yy"
        )
        assert "linkCode" not in out
        assert "linkId" not in out

    def test_expande_url_encurtada(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(url="https://www.amazon.com.br/dp/B0EXPANDIDO")
        monkeypatch.setattr(observable.requests, "get", fake_get)
        out = observable.converter_link_amazon("https://amzn.to/abc")
        assert "B0EXPANDIDO" in out
        assert "tag=meutag-20" in out

    def test_expande_link_amazon(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(url="https://www.amazon.com.br/dp/B0EXPANDIDO")
        monkeypatch.setattr(observable.requests, "get", fake_get)
        out = observable.converter_link_amazon("https://link.amazon/B04w6NHYf")
        assert "B0EXPANDIDO" in out
        assert "tag=meutag-20" in out

    def test_link_amazon_detectado_como_amazon(self):
        assert observable.detectar_plataforma("https://link.amazon/B04w6NHYf") == "amazon"


# ----------------------------------------------------------------------
# converter_link_shopee
# ----------------------------------------------------------------------
class TestConverterShopee:
    def test_sucesso(self, monkeypatch, fake_response):
        def fake_post(url, **kw):
            return fake_response(json_data={
                "data": {"generateShortLink": {"shortLink": "https://s.shopee.com.br/AbC"}}
            })
        monkeypatch.setattr(observable.requests, "post", fake_post)
        assert observable.converter_link_shopee("https://shopee.com.br/x") == "https://s.shopee.com.br/AbC"

    def test_sem_link_retorna_none(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable.requests, "post",
                            lambda *a, **k: fake_response(json_data={"data": {}}))
        assert observable.converter_link_shopee("https://shopee.com.br/x") is None

    def test_excecao_retorna_none(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("timeout")
        monkeypatch.setattr(observable.requests, "post", boom)
        assert observable.converter_link_shopee("https://shopee.com.br/x") is None


# ----------------------------------------------------------------------
# converter_link_aliexpress
# ----------------------------------------------------------------------
class TestConverterAliexpress:
    def test_sucesso(self, monkeypatch, fake_response):
        json_ok = {
            "aliexpress_affiliate_link_generate_response": {
                "resp_result": {"result": {
                    "promotion_links": {"promotion_link": [
                        {"promotion_link": "https://s.click.aliexpress.com/e/abc"}
                    ]}
                }}
            }
        }
        monkeypatch.setattr(observable.requests, "post",
                            lambda *a, **k: fake_response(json_data=json_ok))
        out = observable.converter_link_aliexpress("https://aliexpress.com/item/1.html")
        assert out == "https://s.click.aliexpress.com/e/abc"

    def test_resposta_vazia_retorna_none(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable.requests, "post",
                            lambda *a, **k: fake_response(json_data={}))
        assert observable.converter_link_aliexpress("https://aliexpress.com/item/1.html") is None


# ----------------------------------------------------------------------
# converter_link_kabum
# ----------------------------------------------------------------------
class TestConverterKabum:
    def test_url_direta_sucesso(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable.requests, "post",
                            lambda *a, **k: fake_response(json_data={"shortUrl": "https://tidd.ly/zzz"}))
        out = observable.converter_link_kabum("https://www.kabum.com.br/produto/1?aw_affid=x")
        assert out == "https://tidd.ly/zzz"

    def test_nao_kabum_retorna_none(self):
        # Sem domínio kabum e sem encurtador conhecido => ignora
        assert observable.converter_link_kabum("https://outraloja.com.br/x") is None

    def test_encurtador_expande_para_kabum(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(url="https://www.kabum.com.br/produto/9?utm_source=awin")
        def fake_post(url, **kw):
            return fake_response(json_data={"shortUrl": "https://tidd.ly/final"})
        monkeypatch.setattr(observable.requests, "get", fake_get)
        monkeypatch.setattr(observable.requests, "post", fake_post)
        out = observable.converter_link_kabum("https://tidd.ly/encurtado")
        assert out == "https://tidd.ly/final"

    def test_awin_sem_link_retorna_none(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable.requests, "post",
                            lambda *a, **k: fake_response(json_data={"erro": "x"}))
        assert observable.converter_link_kabum("https://www.kabum.com.br/produto/1") is None


# ----------------------------------------------------------------------
# extrair_url_limpa_kabum (extração do ?ued= em links awin1.com)
# ----------------------------------------------------------------------
class TestExtrairUrlKabum:
    def test_extrai_ued_de_awin(self, monkeypatch, fake_response):
        destino = "https%3A%2F%2Fwww.kabum.com.br%2Fproduto%2F5%3Faw_affid%3Dx"
        def fake_get(url, **kw):
            return fake_response(url=f"https://www.awin1.com/cread.php?ued={destino}")
        monkeypatch.setattr(observable.requests, "get", fake_get)
        out = observable.extrair_url_limpa_kabum("https://tidd.ly/x")
        assert "kabum.com.br/produto/5" in out
        assert "aw_affid" not in out  # parâmetro de terceiro removido


# ----------------------------------------------------------------------
# converter_link_meli (desempacota + converte, tudo mockado)
# ----------------------------------------------------------------------
class TestConverterMeli:
    def test_gera_short_url_com_offer_type(self, monkeypatch, fake_response):
        # desempacotar_link cai direto num produto MLB
        monkeypatch.setattr(observable.requests, "get",
                            lambda *a, **k: fake_response(url="https://www.mercadolivre.com.br/MLB-123456789-produto", text=""))
        capturado = {}

        def fake_post(url, **kw):
            capturado['payload'] = kw.get('json')
            return fake_response(status_code=200, json_data={"short_url": "https://meli.la/2NUbm4v"})
        monkeypatch.setattr(observable.requests, "post", fake_post)

        out = observable.converter_link_meli("https://meli.la/encurtado")
        # retorna o SEU short_url de afiliado
        assert out == "https://meli.la/2NUbm4v"
        # e a URL enviada à API carrega offer_type=BEST_PRICE (chave p/ abrir o produto)
        assert "offer_type=BEST_PRICE" in capturado['payload']['url']
        assert capturado['payload']['tag'] == "ta20250609093813"

    def test_sessao_expirada_retorna_none(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable.requests, "get",
                            lambda *a, **k: fake_response(url="https://www.mercadolivre.com.br/MLB-1-x", text=""))
        monkeypatch.setattr(observable.requests, "post",
                            lambda *a, **k: fake_response(status_code=403, text="forbidden"))
        assert observable.converter_link_meli("https://meli.la/x") is None

    def test_sem_produto_valido_retorna_none(self, monkeypatch, fake_response):
        # desempacotar cai numa página social sem nenhum link de produto
        monkeypatch.setattr(observable.requests, "get",
                            lambda *a, **k: fake_response(url="https://www.mercadolivre.com.br/social/loja", text="<html>nada</html>"))
        assert observable.converter_link_meli("https://meli.la/x") is None


# ----------------------------------------------------------------------
# desempacotar_link
# ----------------------------------------------------------------------
class TestDesempacotarLink:
    def test_produto_direto(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable.requests, "get",
                            lambda *a, **k: fake_response(url="https://www.mercadolivre.com.br/MLB-999-x", text=""))
        out = observable.desempacotar_link("https://meli.la/x")
        assert "MLB-999" in out

    def test_pagina_social_raspa_html(self, monkeypatch, fake_response):
        html = '<a href="https://www.mercadolivre.com.br/produto-MLB-777-abc">ver</a>'
        monkeypatch.setattr(observable.requests, "get",
                            lambda *a, **k: fake_response(url="https://www.mercadolivre.com.br/social/x", text=html))
        out = observable.desempacotar_link("https://meli.la/x")
        assert out is not None
        assert "MLB-777" in out

    def test_erro_de_rede_retorna_none(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("conn reset")
        monkeypatch.setattr(observable.requests, "get", boom)
        assert observable.desempacotar_link("https://meli.la/x") is None


# ----------------------------------------------------------------------
# converter_link (roteamento por plataforma)
# ----------------------------------------------------------------------
class TestConverterLinkRoteamento:
    def test_roteia_para_conversor_certo(self, monkeypatch):
        for nome in ["shopee", "aliexpress", "amazon", "kabum", "meli"]:
            monkeypatch.setattr(observable, f"converter_link_{nome}",
                                lambda link, _n=nome: f"ok-{_n}")

        assert observable.converter_link("https://shopee.com.br/x") == "ok-shopee"
        assert observable.converter_link("https://aliexpress.com/x") == "ok-aliexpress"
        assert observable.converter_link("https://amazon.com.br/x") == "ok-amazon"
        assert observable.converter_link("https://kabum.com.br/x") == "ok-kabum"
        assert observable.converter_link("https://mercadolivre.com.br/MLB-1") == "ok-meli"

    def test_plataforma_desconhecida_retorna_none(self):
        assert observable.converter_link("https://sitequalquer.com/x") is None


# ----------------------------------------------------------------------
# expandir_link_curto (encurtadores genéricos tipo aoferta.net)
# ----------------------------------------------------------------------
class TestExpandirLinkCurto:
    def test_expande_aoferta_para_amazon(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(
                url="https://www.amazon.com.br/dp/B0D2JD6P86?tag=terceiro-20&linkCode=ogi"
            )
        monkeypatch.setattr(observable.requests, "get", fake_get)
        out = observable.expandir_link_curto("https://aoferta.net/000nJkeb-Amazon")
        assert "amazon.com.br/dp/B0D2JD6P86" in out

    def test_dominio_normal_nao_chama_rede(self, monkeypatch):
        def boom(*a, **kw):
            raise AssertionError("não deveria chamar a rede")
        monkeypatch.setattr(observable.requests, "get", boom)
        url = "https://www.amazon.com.br/dp/B0ABC"
        assert observable.expandir_link_curto(url) == url

    def test_erro_de_rede_devolve_a_url_original(self, monkeypatch):
        def fake_get(url, **kw):
            raise Exception("timeout")
        monkeypatch.setattr(observable.requests, "get", fake_get)
        url = "https://aoferta.net/000nJkeb-Amazon"
        assert observable.expandir_link_curto(url) == url

    def test_converter_link_roteia_aoferta_para_amazon(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(
                url="https://www.amazon.com.br/dp/B0D2JD6P86?tag=terceiro-20&linkCode=ogi"
            )
        monkeypatch.setattr(observable.requests, "get", fake_get)
        out = observable.converter_link("https://aoferta.net/000nJkeb-Amazon")
        assert "tag=meutag-20" in out
        assert "terceiro-20" not in out
        assert "B0D2JD6P86" in out

    def test_mensagem_com_aoferta_e_convertida(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(url="https://www.amazon.com.br/dp/B0D2JD6P86")
        monkeypatch.setattr(observable.requests, "get", fake_get)
        texto, ok = observable.substituir_links_no_texto(
            "Fone bom demais https://aoferta.net/000nJkeb-Amazon corre"
        )
        assert ok is True
        assert "aoferta.net" not in texto
        assert "tag=meutag-20" in texto


# ----------------------------------------------------------------------
# substituir_links_no_texto (decide se a mensagem é liberada)
# ----------------------------------------------------------------------
class TestSubstituirLinks:
    def test_sem_link_aborta(self):
        texto, ok = observable.substituir_links_no_texto("mensagem sem nenhum link")
        assert ok is False

    def test_link_desconhecido_aborta(self, monkeypatch):
        # qualquer link de plataforma não suportada bloqueia o envio inteiro
        texto, ok = observable.substituir_links_no_texto("veja https://sitequalquer.com/x")
        assert ok is False

    def test_falha_na_conversao_aborta(self, monkeypatch):
        monkeypatch.setattr(observable, "converter_link", lambda link: None)
        texto, ok = observable.substituir_links_no_texto("https://shopee.com.br/x")
        assert ok is False

    def test_sucesso_substitui_link(self, monkeypatch):
        monkeypatch.setattr(observable, "converter_link", lambda link: "https://afiliado/ok")
        texto, ok = observable.substituir_links_no_texto("Compre em https://shopee.com.br/x agora")
        assert ok is True
        assert "https://afiliado/ok" in texto
        assert "shopee.com.br/x" not in texto

    def test_cupom_ml_com_encurtador_mercadolivre_com_sec(self, monkeypatch, fake_response):
        # Post real do OQMDV (06/10, 15h BRT): o link mercadolivre.com/sec/ cai
        # na vitrine /social/ do afiliado de lá; vira o NOSSO link do produto.
        texto = (
            "NOVO CUPOM MERCADO LIVRE\n\n"
            "✅ 15% OFF acima de R$249, limite R$60\n"
            "🔴 CUPOM: CASA06101010\n\n"
            "✨ SALVE AQUI E SIGA O CANAL PARA AJUDAR:\n"
            "https://mercadolivre.com/sec/1qrUweQ"
        )
        vitrine = '<a href="https://www.mercadolivre.com.br/produto-x/p/MLB16268160#card-featured">ver</a>'
        monkeypatch.setattr(observable.requests, "get", lambda *a, **k: fake_response(
            url="https://www.mercadolivre.com.br/social/ao20251027154024?matt_word=gabriel",
            text=vitrine))
        monkeypatch.setattr(observable.requests, "post", lambda *a, **k: fake_response(
            status_code=200, json_data={"short_url": "https://meli.la/meu"}))

        final, ok = observable.substituir_links_no_texto(texto)

        assert ok is True
        assert "https://meli.la/meu" in final
        assert "mercadolivre.com/sec" not in final
        assert "CUPOM: CASA06101010" in final

    def test_post_da_magalu_nao_e_publicado(self, monkeypatch, fake_response):
        # Sem afiliado Magalu: o post inteiro é descartado, sem nem ir à rede.
        # A rede responde como em produção (um except engoliria um erro falso).
        chamadas = []

        def fake_get(url, **k):
            chamadas.append(url)
            return fake_response(url=url)
        monkeypatch.setattr(observable.requests, "get", fake_get)
        texto = ("Monitor Gamer AOC 32 QHD 180Hz\n💰 R$ 1.299\n"
                 "https://www.magazineluiza.com.br/monitor-gamer-aoc/p/240419000/in/mlcd/"
                 "?partner_id=3440&promoter_id=4511416&utm_source=botanalista")
        final, ok = observable.substituir_links_no_texto(texto)
        assert ok is False
        assert "tag=" not in final
        assert chamadas == []

    def test_um_link_invalido_no_meio_aborta_tudo(self, monkeypatch):
        # primeiro link converte, mas o segundo é desconhecido => aborta a msg
        monkeypatch.setattr(observable, "converter_link", lambda link: "https://afiliado/ok")
        texto = "https://shopee.com.br/a e https://sitequalquer.com/b"
        _, ok = observable.substituir_links_no_texto(texto)
        assert ok is False


# ----------------------------------------------------------------------
# enviar_alerta_expiracao (aviso ao admin quando a sessão do ML cai)
# ----------------------------------------------------------------------
class TestAlertaExpiracao:
    def _mockar(self, monkeypatch, fake_response, enviados):
        """requests.post falso: 403 para a API do ML, 200 para a Bot API."""
        monkeypatch.setattr(observable.requests, "get",
                            lambda *a, **k: fake_response(url="https://www.mercadolivre.com.br/MLB-1-x", text=""))

        def fake_post(url, **kw):
            if "api.telegram.org" in url:
                enviados.append(kw.get('json'))
                return fake_response(status_code=200)
            return fake_response(status_code=403, text="forbidden")

        monkeypatch.setattr(observable.requests, "post", fake_post)

    def test_403_avisa_o_admin(self, monkeypatch, fake_response):
        enviados = []
        self._mockar(monkeypatch, fake_response, enviados)

        assert observable.converter_link_meli("https://meli.la/x") is None

        assert len(enviados) == 1
        assert str(enviados[0]['chat_id']) == "111"          # TELEGRAM_ADMIN_ID
        assert "MELI_COOKIE" in enviados[0]['text']
        assert "MELI_X_CSRF_TOKEN" in enviados[0]['text']

    def test_cooldown_nao_repete_o_alerta(self, monkeypatch, fake_response):
        enviados = []
        self._mockar(monkeypatch, fake_response, enviados)

        observable.converter_link_meli("https://meli.la/x")
        observable.converter_link_meli("https://meli.la/y")

        assert len(enviados) == 1  # rajada de 403 gera um único aviso

    def test_falha_no_envio_nao_entra_em_cooldown(self, monkeypatch, fake_response):
        tentativas = []

        def fake_post(url, **kw):
            if "api.telegram.org" in url:
                tentativas.append(url)
                return fake_response(status_code=500, text="erro")
            return fake_response(status_code=403, text="forbidden")

        monkeypatch.setattr(observable.requests, "get",
                            lambda *a, **k: fake_response(url="https://www.mercadolivre.com.br/MLB-1-x", text=""))
        monkeypatch.setattr(observable.requests, "post", fake_post)

        observable.converter_link_meli("https://meli.la/x")
        observable.converter_link_meli("https://meli.la/y")

        assert len(tentativas) == 2  # aviso não saiu => tenta de novo

    def test_sem_admin_configurado_nao_envia(self, monkeypatch, fake_response):
        enviados = []
        self._mockar(monkeypatch, fake_response, enviados)
        monkeypatch.setattr(observable, "ADMIN_CHAT_ID", None)

        observable.converter_link_meli("https://meli.la/x")

        assert enviados == []
