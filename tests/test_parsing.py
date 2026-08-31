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
# ja_foi_enviado (cache de deduplicação)
# ----------------------------------------------------------------------
class TestJaFoiEnviado:
    def test_primeira_vez_falso_segunda_verdadeiro(self):
        msg = "Monitor LG 27 polegadas\nhttps://shopee.com.br/x"
        assert observable.ja_foi_enviado(msg) is False   # MISS
        assert observable.ja_foi_enviado(msg) is True    # HIT

    def test_mesmo_titulo_canais_diferentes_e_duplicata(self):
        a = "Headset HyperX Cloud\nhttps://shopee.com.br/a"
        b = "Headset HyperX Cloud\nhttps://amazon.com.br/b"
        assert observable.ja_foi_enviado(a) is False
        # mesmo título normalizado => duplicata, mesmo com link diferente
        assert observable.ja_foi_enviado(b) is True

    def test_titulos_diferentes_nao_colidem(self):
        assert observable.ja_foi_enviado("Teclado Mecânico\nhttps://shopee.com.br/a") is False
        assert observable.ja_foi_enviado("Webcam Full HD\nhttps://shopee.com.br/b") is False

    def test_normalizacao_ignora_emoji_e_caixa(self):
        assert observable.ja_foi_enviado("🔥 SSD Kingston 480GB\nhttps://shopee.com.br/a") is False
        # mesmo produto, com caixa/emoji diferentes => duplicata
        assert observable.ja_foi_enviado("ssd kingston 480gb!!!\nhttps://shopee.com.br/b") is True
