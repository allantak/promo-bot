"""
Testes de regressão — parsing de mensagem, formatação e deduplicação.
"""
import observable


# ----------------------------------------------------------------------
# parsear_mensagem
# ----------------------------------------------------------------------
class TestParsearMensagem:
    def test_oferta_normal_titulo_e_preco(self):
        msg = (
            "🔥 Mouse Gamer Logitech G502\n"
            "💰 R$ 199,90\n"
            "🔗 https://shopee.com.br/produto-123"
        )
        d = observable.parsear_mensagem(msg)
        assert d["titulo"] == "🔥 Mouse Gamer Logitech G502"
        assert d["preco"] == "💰 R$ 199,90"
        assert d["link_produto"] == "https://shopee.com.br/produto-123"
        assert d["e_cupom_avulso"] is False

    def test_preco_com_padrao_por(self):
        d = observable.parsear_mensagem("Produto bacana\npor: R$ 49,99\nhttps://shopee.com.br/x")
        assert d["preco"] is not None
        assert "49,99" in d["preco"]

    def test_cupom_avulso_detectado(self):
        msg = "Novo cupom de desconto!\nResgate aqui: https://shopee.com.br/cupom"
        d = observable.parsear_mensagem(msg)
        assert d["e_cupom_avulso"] is True

    def test_cupom_avulso_falso_quando_tem_preco(self):
        # "cupom de desconto" + preço R$ => NÃO é cupom avulso
        msg = "Cupom de desconto no mouse\n💰 R$ 10\nhttps://shopee.com.br/x"
        d = observable.parsear_mensagem(msg)
        assert d["e_cupom_avulso"] is False

    def test_link_produto_ignora_tme(self):
        msg = "Produto legal\nhttps://t.me/canal\nhttps://shopee.com.br/real"
        d = observable.parsear_mensagem(msg)
        assert d["link_produto"] == "https://shopee.com.br/real"

    def test_sem_titulo_quando_so_ha_marcadores(self):
        msg = "💰 R$ 10\n🔗 https://shopee.com.br/x"
        d = observable.parsear_mensagem(msg)
        assert d["titulo"] is None

    def test_quirk_cupom_codigo_captura_palavra_cupom(self):
        # [BUG] A regex (?:cupom|🎟)[:\s]+([A-Z0-9_-]{4,30}) casa o marcador 🎟
        # seguido de espaço e captura a PRÓPRIA palavra "Cupom" como código,
        # em vez do código real (SAVE10).
        msg = (
            "🔥 Mouse Gamer\n"
            "💰 R$ 199,90\n"
            "🎟 Cupom: SAVE10\n"
            "🔗 https://shopee.com.br/produto-123"
        )
        d = observable.parsear_mensagem(msg)
        assert d["cupom_codigo"] == "CUPOM"  # comportamento atual (bugado)

    def test_cupom_codigo_extraido_corretamente_sem_emoji(self):
        # Sem o emoji 🎟 antes, a palavra "Cupom:" casa e captura o código certo.
        msg = "Produto\nCupom: SAVE10\nhttps://shopee.com.br/x"
        d = observable.parsear_mensagem(msg)
        assert d["cupom_codigo"] == "SAVE10"


# ----------------------------------------------------------------------
# formatar_mensagem
# ----------------------------------------------------------------------
class TestFormatarMensagem:
    def test_oferta_normal_inclui_titulo_preco_link(self):
        msg = "Notebook Dell i5\n💰 R$ 3000\nhttps://shopee.com.br/x"
        out = observable.formatar_mensagem(msg, "https://afiliado/x", "shopee")
        assert "Notebook Dell i5" in out
        assert "🛍️" in out          # ícone shopee
        assert "💰" in out
        assert "https://afiliado/x" in out
        assert "<b>" in out

    def test_icone_padrao_para_plataforma_desconhecida(self):
        msg = "Produto\n💰 R$ 1\nhttps://shopee.com.br/x"
        out = observable.formatar_mensagem(msg, "link", "qualquer")
        assert "🔥" in out

    def test_cupom_avulso_formatado(self):
        msg = "Novo cupom de desconto!\nResgate aqui: https://shopee.com.br/c"
        out = observable.formatar_mensagem(msg, "https://afiliado/c", "shopee")
        assert "CUPOM DE DESCONTO" in out
        assert "https://afiliado/c" in out

    def test_titulo_padrao_quando_ausente(self):
        msg = "💰 R$ 10\nhttps://shopee.com.br/x"
        out = observable.formatar_mensagem(msg, "link", "amazon")
        assert "Oferta Encontrada" in out


# ----------------------------------------------------------------------
# chave_dedup_link (chave canônica por produto, sem rede)
# ----------------------------------------------------------------------
class TestChaveDedupLink:
    def test_amazon_mesma_asin_tags_diferentes_colidem(self):
        a = observable.chave_dedup_link("https://www.amazon.com.br/dp/B08XYZ1234?tag=a")
        b = observable.chave_dedup_link(
            "https://www.amazon.com.br/dp/B08XYZ1234?tag=b&utm_source=x"
        )
        assert a == b == "amz:B08XYZ1234"   # ASIN tem exatamente 10 chars

    def test_mercadolivre_mlb_com_fragmento_e_wid_colidem(self):
        a = observable.chave_dedup_link(
            "https://www.mercadolivre.com.br/produto/p/MLB-123456789"
        )
        b = observable.chave_dedup_link(
            "https://www.mercadolivre.com.br/produto/p/MLB123456789#wid=MLB999&x=1"
        )
        assert a == b == "meli:MLB123456789"

    def test_kabum_ignora_tracking_de_terceiro(self):
        a = observable.chave_dedup_link("https://www.kabum.com.br/produto/555?aw_affid=x")
        b = observable.chave_dedup_link("https://www.kabum.com.br/produto/555?utm_source=y")
        assert a == b == "kabum:555"

    def test_produtos_diferentes_nao_colidem(self):
        a = observable.chave_dedup_link("https://www.amazon.com.br/dp/B08XYZ1234")
        b = observable.chave_dedup_link("https://www.amazon.com.br/dp/B000OUTRO1")
        assert a != b

    def test_link_vazio(self):
        assert observable.chave_dedup_link("") == ""


# ----------------------------------------------------------------------
# ja_foi_enviado (cache de deduplicação — agora pelo LINK)
# ----------------------------------------------------------------------
class TestJaFoiEnviado:
    def test_primeira_vez_falso_segunda_verdadeiro(self):
        msg = "Monitor LG 27 polegadas\nhttps://www.amazon.com.br/dp/B08MONIT01"
        assert observable.ja_foi_enviado(msg) is False   # MISS
        assert observable.ja_foi_enviado(msg) is True    # HIT

    def test_mesmo_titulo_links_diferentes_nao_e_duplicata(self):
        # mesmo título, mas produtos (links) diferentes => NÃO é duplicata
        a = "Headset HyperX Cloud\nhttps://www.amazon.com.br/dp/B08HEADS01"
        b = "Headset HyperX Cloud\nhttps://www.amazon.com.br/dp/B08HEADS02"
        assert observable.ja_foi_enviado(a) is False
        assert observable.ja_foi_enviado(b) is False

    def test_mesmo_produto_titulo_e_tracking_diferentes_e_duplicata(self):
        # mesmo produto (mesma ASIN) com título/tracking diferentes => duplicata
        a = "🔥 SSD Kingston 480GB\nhttps://www.amazon.com.br/dp/B08SSD0001?tag=a"
        b = "ssd kingston!!!\nhttps://www.amazon.com.br/dp/B08SSD0001?tag=b&utm_source=z"
        assert observable.ja_foi_enviado(a) is False
        assert observable.ja_foi_enviado(b) is True

    def test_produtos_diferentes_nao_colidem(self):
        assert observable.ja_foi_enviado(
            "Teclado Mecânico\nhttps://www.amazon.com.br/dp/B08TECLAD1"
        ) is False
        assert observable.ja_foi_enviado(
            "Webcam Full HD\nhttps://www.amazon.com.br/dp/B08WEBCAM1"
        ) is False

    def test_sem_link_nao_bloqueia(self):
        assert observable.ja_foi_enviado("Oferta sem link nenhum") is False


# ----------------------------------------------------------------------
# chave_dedup_link com encurtadores (redirect mockado)
# ----------------------------------------------------------------------
class TestChaveDedupLinkEncurtador:
    def test_dois_encurtadores_amazon_mesma_asin_colidem(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(url="https://www.amazon.com.br/dp/B08REAL001")
        monkeypatch.setattr(observable.requests, "get", fake_get)
        a = observable.chave_dedup_link("https://amzn.to/aaa")
        b = observable.chave_dedup_link("https://amzn.to/bbb")
        assert a == b == "amz:B08REAL001"

    def test_meli_encurtado_resolve_para_mlb(self, monkeypatch, fake_response):
        def fake_get(url, **kw):
            return fake_response(url="https://www.mercadolivre.com.br/MLB-321-x", text="")
        monkeypatch.setattr(observable.requests, "get", fake_get)
        curto = observable.chave_dedup_link("https://meli.la/encurtado")
        completo = observable.chave_dedup_link(
            "https://www.mercadolivre.com.br/produto/p/MLB-321"
        )
        assert curto == completo == "meli:MLB321"

    def test_falha_de_rede_cai_no_fallback(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("timeout")
        monkeypatch.setattr(observable.requests, "get", boom)
        # não quebra; devolve chave de fallback determinística (host+path)
        assert observable.chave_dedup_link("https://amzn.to/xyz") == "amzn.to/xyz"
