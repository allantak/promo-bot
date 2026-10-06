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
        # encurtador novo do ML (sem o .br) — o OQMDV já usa nos posts
        assert observable.detectar_plataforma("https://mercadolivre.com/sec/1qrUweQ") == "mercadolivre"

    def test_desconhecido(self):
        assert observable.detectar_plataforma("https://google.com") == "desconhecido"

    def test_dominio_que_contem_a_co_nao_e_amazon(self):
        # Antes a checagem era por substring e 'a.co' casava com
        # "magazineluiz-A.CO-m.br": o link da Magalu (com o promoter_id de
        # outro divulgador) saía publicado como se fosse Amazon.
        for url in ["https://www.magazineluiza.com.br/x/p/240419000/?promoter_id=4511416",
                    "https://www.casasbahia.com.br/x",
                    "https://www.kalunga.com.br/x"]:
            assert observable.detectar_plataforma(url) == "desconhecido"

    def test_dominio_da_loja_so_no_texto_do_link_nao_conta(self):
        # O que vale é o domínio do link, não um pedaço da query/caminho.
        assert observable.detectar_plataforma(
            "https://sitequalquer.com/r?u=https://www.amazon.com.br/dp/B0") == "desconhecido"

    def test_subdominio_e_maiusculas(self):
        assert observable.detectar_plataforma("https://m.amazon.com.br/dp/B0") == "amazon"
        assert observable.detectar_plataforma("HTTPS://AMZN.TO/x") == "amazon"
        assert observable.detectar_plataforma("https://produto.mercadolivre.com.br/MLB-1") == "mercadolivre"

    def test_awin_vai_para_o_conversor_da_kabum(self):
        # O converter_link_kabum expande o awin1.com e descarta se não for KaBuM.
        assert observable.detectar_plataforma(
            "https://www.awin1.com/cread.php?awinmid=17729&ued=https%3A%2F%2Fwww.kabum.com.br%2Fproduto%2F1") == "kabum"


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

    def test_jogo_de_videogame_passa(self):
        # 'jogo' sozinho barrava jogos de PS5; agora só "jogo de chave" etc.
        assert observable.e_do_nicho("Jogo GTA VI PS5") is True
        assert observable.e_do_nicho("Controle para jogos Machenike G3 V2") is True
        assert observable.e_do_nicho("Jogo de chaves Tramontina 40 peças") is False
        assert observable.e_do_nicho("Jogo De Chave Combinada 8 Peças") is False
        assert observable.e_do_nicho("Jogo de Facas Tramontina") is False

    def test_termos_que_antes_casavam_por_substring_continuam_barrados(self):
        # Sem a substring, 'calça' não pega mais "calçado" e 'tênis' não pega
        # "sapatênis": esses termos entraram na lista explicitamente.
        assert observable.e_do_nicho("Calçado Social Masculino") is False
        assert observable.e_do_nicho("Sapatênis Casual Couro") is False
        assert observable.e_do_nicho("Kit 6 Xícaras de Porcelana") is False

    def test_tv_continua_barrada(self):
        assert observable.e_do_nicho("Smart TV 50 polegadas") is False
        assert observable.e_do_nicho("Kit 2 TVs LG 43") is False

    def test_plural_continua_barrado(self):
        assert observable.e_do_nicho("Kit 3 Camisetas Dry Fit") is False
        assert observable.e_do_nicho("Conjunto 5 Panelas antiaderente") is False

    def test_termos_com_maiuscula_na_lista_agora_filtram(self):
        # 'MacBook', 'Apple Watch', 'AirPods' estavam na lista com maiúscula e
        # nunca casavam com o texto em minúsculas.
        assert observable.e_do_nicho("MacBook Air M3 13") is False
        assert observable.e_do_nicho("Apple Watch SE 2") is False

    # Falsos positivos reais medidos nos canais (27/09–01/10/2026): o filtro
    # casava o termo como PEDAÇO de outra palavra ou dentro de URLs/cupons.
    def test_palavra_dentro_de_outra_nao_barra(self):
        assert observable.e_do_nicho("Gabinete Gamer Lian Li Janela Lateral") is True       # anel
        assert observable.e_do_nicho("Notebook Gamer Acer Nitro V 15 13ª Geração") is True  # ração
        assert observable.e_do_nicho("Carregador Baseus 140W GaN") is True                  # regador, base
        assert observable.e_do_nicho("Monitor LG UltraGear painel IPS alta performance") is True  # anel, forma

    def test_termos_ambiguos_nao_barram_produto_tech(self):
        assert observable.e_do_nicho('Monitor Gamer AOC 27" Base Ajustável') is True
        assert observable.e_do_nicho("Gabinete Gamer Aquário Pichau Atom") is True
        assert observable.e_do_nicho("Notebook Lenovo Yoga Slim 7") is True
        texto_ali = ('Microfone Fifine Ampligame A2\n'
                     'Após entrar no link no APP, vá na guia "🇧🇷 Do Brasil"')
        assert observable.e_do_nicho(texto_ali) is True

    def test_url_e_cupom_nao_contam(self):
        # Slug com termo bloqueado como palavra inteira: só passa porque a URL é ignorada.
        assert observable.e_do_nicho(
            "Mouse Logitech G305\nhttps://www.mercadolivre.com.br/smart-tv-samsung/p/MLB1") is True
        assert observable.e_do_nicho("SSD Kingston NV3 1TB\n🎟️ Cupom: TVS3009") is True


# ----------------------------------------------------------------------
# PALAVRAS_FORA_NICHO — vírgula que faltava entre 'powerbank' e 'vitamina'
# ----------------------------------------------------------------------
class TestBlocklistVirgula:
    def test_powerbank_e_vitamina_sao_termos_separados(self):
        assert "powerbankvitamina" not in observable.PALAVRAS_FORA_NICHO
        assert "vitamina" in observable.PALAVRAS_FORA_NICHO
        assert "powerbank" in observable.PALAVRAS_FORA_NICHO

    def test_vitamina_filtra(self):
        assert observable.e_do_nicho("Vitamina C 1000mg") is False


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
