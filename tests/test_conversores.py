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

    def test_um_link_invalido_no_meio_aborta_tudo(self, monkeypatch):
        # primeiro link converte, mas o segundo é desconhecido => aborta a msg
        monkeypatch.setattr(observable, "converter_link", lambda link: "https://afiliado/ok")
        texto = "https://shopee.com.br/a e https://sitequalquer.com/b"
        _, ok = observable.substituir_links_no_texto(texto)
        assert ok is False
