"""
Testes de regressão — funções puras (sem rede).

Estes testes fixam o COMPORTAMENTO ATUAL do código. Se um teste quebrar
no futuro, foi porque o comportamento mudou — aí decida se foi de propósito.
Casos marcados como [QUIRK] documentam comportamentos provavelmente não
intencionais (bugs) que estão valendo hoje.
"""
import observable


# ----------------------------------------------------------------------
# detectar_plataforma
# ----------------------------------------------------------------------
class TestDetectarPlataforma:
    def test_kabum_dominio_direto(self):
        assert observable.detectar_plataforma("https://www.kabum.com.br/produto/1") == "kabum"

    def test_kabum_encurtadores(self):
        for dom in ["https://tidd.ly/x", "https://eioferta.com.br/x", "https://ofertou.xyz/x"]:
            assert observable.detectar_plataforma(dom) == "kabum"

    def test_shopee(self):
        assert observable.detectar_plataforma("https://shopee.com.br/x") == "shopee"
        assert observable.detectar_plataforma("https://shope.ee/x") == "shopee"

    def test_aliexpress(self):
        assert observable.detectar_plataforma("https://pt.aliexpress.com/item/1.html") == "aliexpress"
        assert observable.detectar_plataforma("https://s.click.aliexpress.com/e/x") == "aliexpress"

    def test_amazon(self):
        assert observable.detectar_plataforma("https://www.amazon.com.br/dp/B0") == "amazon"
        assert observable.detectar_plataforma("https://amzn.to/x") == "amazon"
        assert observable.detectar_plataforma("https://a.co/x") == "amazon"

    def test_mercadolivre(self):
        assert observable.detectar_plataforma("https://mercadolivre.com.br/MLB-1") == "mercadolivre"
        assert observable.detectar_plataforma("https://meli.la/x") == "mercadolivre"
        assert observable.detectar_plataforma("https://meli.bz/x") == "mercadolivre"

    def test_desconhecido(self):
        assert observable.detectar_plataforma("https://google.com") == "desconhecido"

    def test_quirk_substring_a_co_classifica_como_amazon(self):
        # [BUG] A checagem da Amazon procura a substring 'a.co'. Qualquer
        # domínio que contenha "a.co" (ex.: "magazineluiz-A.CO-m.br") é
        # classificado como amazon por engano. Vale para muitos domínios
        # terminados em "...a.com".
        assert observable.detectar_plataforma("https://magazineluiza.com.br/x") == "amazon"


# ----------------------------------------------------------------------
# remover_rodape
# ----------------------------------------------------------------------
class TestRemoverRodape:
    def test_remove_gato_e_alertabot(self):
        texto = (
            "Produto X\n"
            "💰 R$ 10\n"
            "🔗 https://shopee.com.br/x\n"
            "🐈 t.me/OQMCUPONS\n"
            "🔔 Receba notificações com @OQMALERTABOT"
        )
        limpo = observable.remover_rodape(texto)
        assert "🐈" not in limpo
        assert "ALERTABOT" not in limpo
        assert "Receba notifica" not in limpo
        assert "Produto X" in limpo
        assert "https://shopee.com.br/x" in limpo

    def test_remove_link_tme(self):
        assert "t.me" not in observable.remover_rodape("linha\nveja t.me/canal")

    def test_colapsa_linhas_em_branco(self):
        texto = "A\n\n\n\nB"
        assert observable.remover_rodape(texto) == "A\n\nB"

    def test_texto_sem_rodape_inalterado(self):
        texto = "Oferta boa\n💰 R$ 99"
        assert observable.remover_rodape(texto) == texto


# ----------------------------------------------------------------------
# extrair_id_mlb / extrair_buy_box_winner / limpar_url_produto
# ----------------------------------------------------------------------
class TestMercadoLivreUrls:
    def test_extrair_id_mlb_simples(self):
        assert observable.extrair_id_mlb("https://produto.mercadolivre.com.br/MLB-123-abc") == "MLB123"

    def test_extrair_id_mlb_sem_match(self):
        assert observable.extrair_id_mlb("https://google.com") == ""

    def test_buy_box_prioriza_wid(self):
        url = "https://www.mercadolivre.com.br/p/MLB-123?wid=MLB456"
        assert observable.extrair_buy_box_winner(url) == "MLB456"

    def test_buy_box_usa_pdp_filters(self):
        url = "https://www.mercadolivre.com.br/p/MLB-999?pdp_filters=item_id%3AMLB-555"
        assert observable.extrair_buy_box_winner(url) == "MLB555"

    def test_buy_box_fallback_path(self):
        url = "https://produto.mercadolivre.com.br/MLB-4677093913-titulo"
        assert observable.extrair_buy_box_winner(url) == "MLB4677093913"

    def test_buy_box_sem_match(self):
        assert observable.extrair_buy_box_winner("https://google.com") == ""

    def test_buy_box_decodifica_amp(self):
        url = "https://x.com/p/MLB-1?wid=MLB2&amp;foo=bar"
        assert observable.extrair_buy_box_winner(url) == "MLB2"

    def test_limpar_url_mantem_so_essenciais(self):
        url = "https://x.com/p/MLB-1?wid=MLB2&matt_tool=abc&tracking_id=zzz&pdp_filters=f"
        limpa = observable.limpar_url_produto(url)
        assert "wid=MLB2" in limpa
        assert "pdp_filters=f" in limpa
        assert "matt_tool" not in limpa
        assert "tracking_id" not in limpa

    def test_limpar_url_sem_query(self):
        url = "https://x.com/p/MLB-1"
        assert observable.limpar_url_produto(url) == "https://x.com/p/MLB-1"


# ----------------------------------------------------------------------
# limpar_url_kabum
# ----------------------------------------------------------------------
class TestLimparUrlKabum:
    def test_remove_params_de_terceiro(self):
        url = "https://www.kabum.com.br/produto/1?aw_affid=x&awc=y&utm_source=z&cod=123"
        limpa = observable.limpar_url_kabum(url)
        assert "aw_affid" not in limpa
        assert "awc" not in limpa
        assert "utm_source" not in limpa
        assert "cod=123" in limpa

    def test_url_limpa_inalterada(self):
        url = "https://www.kabum.com.br/produto/1"
        assert observable.limpar_url_kabum(url) == url


# ----------------------------------------------------------------------
# e_do_nicho
# ----------------------------------------------------------------------
class TestEDoNicho:
    def test_dentro_do_nicho(self):
        assert observable.e_do_nicho("Mouse gamer RGB com fio") is True
        assert observable.e_do_nicho("Placa de vídeo RTX 4060") is True

    def test_rejeita_palavra_bloqueada(self):
        assert observable.e_do_nicho("Camiseta branca masculina") is False
        assert observable.e_do_nicho("Geladeira Brastemp 375L") is False

    def test_match_e_case_insensitive(self):
        assert observable.e_do_nicho("CAMISA POLO") is False

    def test_quirk_substring_jogo_rejeitado(self):
        # [QUIRK] 'jogo' está na blocklist (categoria ferramentas) e o filtro
        # usa 'palavra in texto', então QUALQUER menção a "jogo" é barrada,
        # inclusive jogos de videogame — provável falso positivo.
        assert observable.e_do_nicho("Jogo para PS5") is False

    def test_quirk_substring_tv(self):
        # [QUIRK] 'tv' é substring; "Smart TV" é barrado (intencional p/ TVs),
        # mas qualquer palavra contendo "tv" também seria.
        assert observable.e_do_nicho("Smart TV 50 polegadas") is False


# ----------------------------------------------------------------------
# PALAVRAS_FORA_NICHO — bug da vírgula faltando
# ----------------------------------------------------------------------
class TestBlocklistBugVirgula:
    def test_quirk_powerbank_vitamina_concatenados(self):
        # [BUG] Falta uma vírgula entre 'powerbank' e 'vitamina' no código,
        # então o Python concatena os literais e cria 'powerbankvitamina'.
        # Resultado: nem "powerbank" nem "vitamina" filtram sozinhos.
        assert "powerbankvitamina" in observable.PALAVRAS_FORA_NICHO
        assert "vitamina" not in observable.PALAVRAS_FORA_NICHO
        assert "powerbank" not in observable.PALAVRAS_FORA_NICHO

    def test_quirk_vitamina_nao_filtra(self):
        # Por causa do bug acima, um produto "vitamina" passa pelo filtro.
        assert observable.e_do_nicho("Vitamina C 1000mg") is True


# ----------------------------------------------------------------------
# assinar_requisicao_ali — determinística
# ----------------------------------------------------------------------
class TestAssinaturaAli:
    def test_hash_conhecido(self):
        params = {"a": "1", "b": "2"}
        esperado = "19167B46D8DE7FA0892679C1EA0DF82163A8A5044F7C32BFD9B2CF79A76C4AFD"
        assert observable.assinar_requisicao_ali(params, "segredo") == esperado

    def test_ordem_dos_params_nao_importa(self):
        a = observable.assinar_requisicao_ali({"a": "1", "b": "2"}, "s")
        b = observable.assinar_requisicao_ali({"b": "2", "a": "1"}, "s")
        assert a == b

    def test_saida_em_maiuscula_e_64_hex(self):
        h = observable.assinar_requisicao_ali({"x": "y"}, "s")
        assert h == h.upper()
        assert len(h) == 64
