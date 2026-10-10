"""
Testes do histórico de preços (historico_precos.py): leitura do preço nos
formatos reais dos canais, identidade do produto, comparação com a média do
mês passado/atual e o banco agregado. Sem rede; o banco vai para tmp_path
(fixture historico_isolado do conftest).
"""
import subprocess
import sys
import threading
from datetime import datetime, timezone

import pytest

import historico_precos as hp


def _utc(ano, mes, dia, hora=15):
    return datetime(ano, mes, dia, hora, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# Preço: linhas reais dos canais @PoisonPromos e @OQMDVPROMO (out/2026)
# ----------------------------------------------------------------------
class TestAnalisarPreco:
    @pytest.mark.parametrize("texto, centavos", [
        ("Notebook Positivo Vision R15M\n- Ryzen 5 5625U\n💵 R$ 2640 no pix\n🎟 Cupom: BOLSOCHEIO", 264000),
        ("Monitor Gamer Tcl 25\n💰POR: R$ 1166\n🔖 Cupom: CELULAR0910", 116600),
        ("Placa de Vídeo ASRock Radeon RX 7600 8GB Challenger OC GDDR6 HDMI 2.1\nPOR: 1799\nCUPOM: CELULAR0910", 179900),
        ("Notebook Lenovo IdeaPad Slim 3\n💰POR: R$ 3086 REAIS (no pix)\n🔖 Cupom: 10DO10", 308600),
        ("Monitor TCL\n💵 R$ 1.166 no PIX", 116600),
        ("Memória DDR5 16GB 5600MHz\n💵 R$ 1.378", 137800),
        ("Notebook Gamer Lenovo Loq\n💵 R$ 5.399,00 no PIX", 539900),
        ("Notebook HP 200 G2i\n- 16GB RAM\n💵 R$ 3293 (PIX)", 329300),
        ("Placa Mãe ASUS B650M-AYW\n💰POR: R$ 679.99\n🔖 Cupom: KORUJAO15", 67999),
        ("Kit Upgrade Gamer\n\n💵 R$ 1.397,00\n\n🎟️ Cupom: `C0M3C0U200`", 139700),
        ("Monitor\nValor: R$4463", 446300),
        ("Kit\n💰 R$ 3494 com cupom: `C0M3C0U200`", 349400),
        ("LG Soundbar SH5A\n\n💰POR: R$ 854 REAIS E FRETE GRÁTIS PRIME", 85400),
        ("Memoria Ram DDR5 8GB\n\npor: 643 reais\n\nRESGATE O CUPOM DE 50 OFF AQUI:", 64300),
    ])
    def test_formatos_reais(self, texto, centavos):
        preco, motivo = hp.analisar_preco(texto)
        assert motivo == "ok"
        assert preco.centavos == centavos

    def test_de_por_pega_o_por(self):
        preco, _ = hp.analisar_preco("impressora Epson\n💰 De ~~R$ 1.447,25~~ por R$ 909,14")
        assert preco.centavos == 90914
        preco, _ = hp.analisar_preco("Cadeira\n💰POR: De R$ 509,90 por R$ 157,32 😱😱")
        assert preco.centavos == 15732

    def test_pix_ganha_do_cartao(self):
        preco, _ = hp.analisar_preco("Notebook Acer\n💵 R$4463 no PIX ou R$4799 em 10x s/ juros")
        assert (preco.centavos, preco.tipo) == (446300, "pix")
        preco, _ = hp.analisar_preco(
            "Monitor LG\n💰POR: R$ 620 REAIS (NO PIX) OU 653 REAIS EM 10X SEM JUROS")
        assert preco.centavos == 62000
        preco, _ = hp.analisar_preco("Notebook\nPOR: 3355 REAIS (pix)\nou 3639 em 12x no cartão")
        assert preco.centavos == 335500

    def test_total_parcelado_vale_como_preco(self):
        # "R$ 649 em 10x sem juros" é o preço cheio (no ML é o mesmo à vista).
        preco, motivo = hp.analisar_preco("Monitor Gigabyte 25\n💵 R$ 649 em 10x sem juros")
        assert (motivo, preco.centavos, preco.tipo) == ("ok", 64900, "cartao")
        preco, _ = hp.analisar_preco("⭐PARCELADO\n\nMonitor X\n💰 R$ 2.199")
        assert preco.tipo == "cartao"

    def test_valor_de_cupom_minimo_e_teto_nao_sao_preco(self):
        texto = ("Ar Condicionado Split 12.000 BTU/h\n💵 Por: R$ 1.739\n"
                 "🎟 Resgate R$100 OFF aqui\nCUPOM DE R$ 40 OFF\nacima de R$ 119, limite R$ 60")
        preco, _ = hp.analisar_preco(texto)
        assert preco.centavos == 173900

    def test_cupom_de_valor_nao_e_subtraido(self):
        texto = "APENAS PELO APLICATIVO🔥\n\nGabinete Mancer CV450\n\nPOR: 281\n\nRESGATE O CUPOM DE R$ 40 OFF"
        assert hp.analisar_preco(texto)[0].centavos == 28100

    def test_numeros_do_titulo_e_rodape_sao_ignorados(self):
        texto = ("Notebook Gamer RTX 4050 16GB 300hz 1920X1200 R$ 9.999\n"
                 "💰POR: R$ 185\n(ANUNCIO)\n🏆Amazon prime\nR$ 7.777")
        assert hp.analisar_preco(texto)[0].centavos == 18500

    @pytest.mark.parametrize("texto, motivo", [
        ("NOVO CUPOM MERCADO LIVRE\n\n✅ 15% OFF acima de R$249, limite R$60\n🔴 CUPOM: CASA06101010", "cupom"),
        ("Cupom Mercado Livre\n\n🎟️ R$ 15 OFF a partir de R$ 99: `SITE10`", "cupom"),
        ("ALERTA de Cupom Mercado Livre! SOMENTE ENTREGAS FULL\n30% OFF em R$ 119, Limitado a R$ 40", "cupom"),
        ("NOVO CUPOM MAGALU!\n\n✅ R$100 OFF em R$1000", "cupom"),
        ("Placa de vídeo X\nPOR: R$ 999\nESGOTADO", "cupom"),
        ("Suporte Articulado\n\n💵 R$ 76,86 para clientes VIP", "exclusivo"),
        ("10x NIVEA Loção\n\n💰POR: R$ 56,64\n🔖 Marque recorrência", "assinatura"),
        ("3 UNIDADES\n\nSsd 1tb Sata 3 Darkplayer\n\nPOR: 585", "quantidade"),
        ("Produto sem preço\nhttps://meli.la/x", "sem_preco"),
        ("Monitor\n💰 R$ 2", "fora_da_faixa"),
        ("", "sem_preco"),
    ])
    def test_posts_que_nao_entram(self, texto, motivo):
        assert hp.analisar_preco(texto) == (None, motivo)

    def test_titulo_do_post(self):
        assert hp.titulo_do_post("⭐PARCELADO\n\nMonitor LG UltraGear 24\n💰 R$ 2") == "Monitor LG UltraGear 24"


# ----------------------------------------------------------------------
# Identidade do produto
# ----------------------------------------------------------------------
class TestChaveDoProduto:
    @pytest.mark.parametrize("url, chave", [
        ("https://www.mercadolivre.com.br/placa-de-video-asrock-rx-7600/p/MLB26915018?offer_type=BEST_PRICE",
         "meli:p:MLB26915018"),
        ("https://www.mercadolivre.com.br/notebook-hp-200-g2i/up/MLBU4972245103", "meli:u:MLBU4972245103"),
        ("https://produto.mercadolivre.com.br/MLB-4001346826-par-manivela-preta-_JM", "meli:i:MLB4001346826"),
        ("https://www.amazon.com.br/Placa-V%C3%ADdeo-GALAX-GeForce-Serious/dp/B09BB3DM7M?tag=x-20",
         "amz:B09BB3DM7M"),
        ("https://www.amazon.com.br/dp/B0FXHQ977Q?tag=bless0ba-20&amp;linkCode=ogi", "amz:B0FXHQ977Q"),
        ("https://www.kabum.com.br/produto/1048336/processador-amd-ryzen-5-7600x3d", "kabum:1048336"),
        ("https://shopee.com.br/Suporte-Articulado-Zinnia-i.627750190.19998132816?gads_t_sig=x",
         "shopee:627750190.19998132816"),
        ("https://shopee.com.br/product/627750190/19998132816", "shopee:627750190.19998132816"),
        ("https://m.aliexpress.com/p/coin-index/index.html?productIds=1005012695916867&aff_fcid=x",
         "ali:1005012695916867"),
        ("https://pt.aliexpress.com/item/1005001234567890.html", "ali:1005001234567890"),
    ])
    def test_urls_de_produto(self, url, chave):
        assert hp.chave_do_produto(url) == chave

    @pytest.mark.parametrize("url", [
        "https://www.mercadolivre.com.br/c/livros-revistas-e-comics",
        "https://www.mercadolivre.com.br/social/ao20251027154024",
        "https://meli.la/31PpTyx",
        "https://mercadolivre.com/sec/1qrUweQ",
        "https://www.amazon.com.br/prime?primeCampaignId=prime_assoc_ft&tag=brocolis-20",
        "https://www.amazon.com.br/promotion/psp/A72ZI52B5PSRF",
        "https://amzn.to/4lM3PHH",
        "https://shopee.com.br/m/cupom-de-desconto?mmp_pid=an_1",
        "https://s.shopee.com.br/5LCYtiIXOL",
        "https://tidd.ly/4ydoEQV",
        "https://www.awin1.com/cread.php?ued=https%3A%2F%2Fwww.kabum.com.br%2Fproduto%2F1048336",
        "https://www.kabum.com.br.golpe.com/produto/123",     # host falsificado
        "https://golpe.com/www.kabum.com.br/produto/123",
        "",
    ])
    def test_urls_que_nao_identificam_produto(self, url):
        assert hp.chave_do_produto(url) is None


class TestChaveUnica:
    TEXTO = "Placa de Vídeo ASRock Radeon RX 7600 8GB Challenger OC\nPOR: 1799"
    URL = "https://www.mercadolivre.com.br/placa-de-video-asrock-radeon-rx-7600-8gb-challenger-oc/p/MLB26915018"

    def test_mesma_chave_em_varias_urls(self):
        urls = [self.URL, self.URL + "?offer_type=BEST_PRICE", "https://meli.la/1mYgCED"]
        assert hp.chave_unica(urls, self.TEXTO) == ("meli:p:MLB26915018", "ok")

    def test_sem_chave(self):
        assert hp.chave_unica(["https://meli.la/x"], self.TEXTO) == (None, "sem_chave")

    def test_duas_chaves_diferentes(self):
        urls = [self.URL, "https://www.kabum.com.br/produto/1/placa-de-video-asrock-radeon-rx-7600"]
        assert hp.chave_unica(urls, self.TEXTO) == (None, "chaves_multiplas")

    def test_vitrine_que_trouxe_outro_produto(self):
        # Caso real (10/10): post de controle de Xbox cuja vitrine do ML devolveu calcinhas.
        url = "https://produto.mercadolivre.com.br/MLB-2912316808-10-calcinhas-fio-renda-lateral-microfibra-_JM"
        assert hp.chave_unica([url], "Controle Xbox Wireless Series X\n💰POR: R$ 228") == (None, "slug_nao_bate")

    def test_nome_curto_que_bate(self):
        # Caso real (16/09): "INSIDER CORE" × camiseta-core-insider.
        url = "https://produto.mercadolivre.com.br/MLB-3996373493-camiseta-core-insider-_JM"
        assert hp.chave_unica([url], "INSIDER CORE\n💸 R$ 66 no PIX")[1] == "ok"

    def test_url_sem_nome_legivel_passa(self):
        assert hp.chave_unica(["https://www.amazon.com.br/dp/B0FXHQ977Q"], "Soundbar LG\nR$ 854")[1] == "ok"


# ----------------------------------------------------------------------
# Datas, formatação
# ----------------------------------------------------------------------
class TestDatasEFormato:
    def test_madrugada_utc_ainda_e_o_dia_anterior_no_brasil(self):
        assert hp.dia_e_mes_brt(datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc)) == ("2026-09-30", "2026-09")

    def test_virada_de_ano(self):
        assert hp.mes_anterior("2026-01") == "2025-12"
        assert hp.deslocar_mes("2026-10", -13) == "2025-09"

    @pytest.mark.parametrize("centavos, texto", [
        (139900, "R$ 1.399"), (139990, "R$ 1.399,90"), (7686, "R$ 76,86"), (100000000, "R$ 1.000.000"),
    ])
    def test_formatar_reais(self, centavos, texto):
        assert hp.formatar_reais(centavos) == texto


# ----------------------------------------------------------------------
# Classificação (função pura)
# ----------------------------------------------------------------------
def _agregado(*precos):
    """(n, soma, minimo, maximo) de uma lista de preços em reais."""
    centavos = [p * 100 for p in precos]
    return (len(centavos), sum(centavos), min(centavos), max(centavos))


class TestClassificar:
    def _classificar(self, preco, setembro=None, outubro=None):
        agregados = {}
        if setembro:
            agregados["2026-09"] = _agregado(*setembro)
        if outubro:
            agregados["2026-10"] = _agregado(*outubro)
        return hp.classificar("meli:p:MLB1", preco * 100, "2026-10", agregados)

    def test_sem_historico(self):
        assert self._classificar(1000).status == "sem_historico"

    def test_uma_promocao_so_nao_vira_referencia(self):
        assert self._classificar(800, setembro=[1000]).status == "sem_historico"

    def test_so_mes_passado(self):
        av = self._classificar(940, setembro=[1000, 1000])
        assert (av.status, [r.mes for r in av.referencias], av.referencias[0].pct) == ("bom", ["2026-09"], 6)

    def test_so_mes_atual(self):
        # Shopee/Ali começam sem setembro: o mês atual já serve de referência.
        av = self._classificar(900, outubro=[1000, 1000])
        assert (av.status, [r.mes for r in av.referencias]) == ("bom", ["2026-10"])

    def test_limiar_de_5_por_cento_com_piso(self):
        assert self._classificar(951, setembro=[1000, 1000]).status == "na_media"   # 4,9%
        assert self._classificar(950, setembro=[1000, 1000]).status == "bom"        # 5%

    def test_excelente_a_partir_de_15_por_cento(self):
        assert self._classificar(850, setembro=[1000, 1000], outubro=[1000, 1000]).status == "excelente"

    def test_menor_preco_desde_o_mes_passado_promove_para_excelente(self):
        av = self._classificar(900, setembro=[950, 1100], outubro=[960, 1000])
        assert av.menor_desde == "2026-09"
        assert av.status == "excelente"

    def test_menor_preco_com_pouco_desconto_nao_promove(self):
        # Menor desde setembro, mas só 5% abaixo: "bom", não "excelente".
        av = self._classificar(950, setembro=[990, 1010], outubro=[990, 1010])
        assert av.menor_desde == "2026-09"
        assert av.status == "bom"

    def test_menor_preco_so_do_mes_atual_nao_promove(self):
        av = self._classificar(900, setembro=[880, 1100], outubro=[960, 1000, 980, 990])
        assert av.menor_desde == "2026-10"
        assert av.status == "bom"

    def test_recorde_exige_historico(self):
        assert self._classificar(800, setembro=[1000, 1000]).menor_desde is None

    def test_abaixo_de_um_mes_mas_bem_acima_do_outro_nao_comenta(self):
        # Setembro barato, outubro caro: 1.000 está 9% abaixo de outubro mas
        # 11% acima de setembro — houve promoção melhor, não é "bom momento".
        av = self._classificar(1000, setembro=[900, 900], outubro=[1100, 1100])
        assert av.status == "misto"
        assert hp.texto_comentario(av) is None

    def test_acima_da_media(self):
        assert self._classificar(1100, setembro=[1000, 1000]).status == "acima"

    def test_suspeito_nao_comenta(self):
        av = self._classificar(450, setembro=[1000, 1000])
        assert av.status == "suspeito"
        assert hp.texto_comentario(av) is None

    def test_referencia_dispersa_e_descartada(self):
        # 8 GB a R$ 180 e 16 GB a R$ 320 sob o mesmo anúncio: média sem sentido.
        assert self._classificar(150, setembro=[180, 320]).status == "sem_historico"

    def test_reajuste_das_placas_de_video(self):
        # Setembro todo no patamar novo (R$ 1.799): o preço normal fica "na
        # média" e só uma queda de verdade vira comentário.
        assert self._classificar(1799, setembro=[1799, 1779, 1799, 1799]).status == "na_media"
        assert self._classificar(1699, setembro=[1799, 1779, 1799, 1799]).status == "bom"
        # Aumento no meio de outubro: o preço novo fica acima de setembro e o
        # bot não comenta, mesmo abaixo da média de outubro.
        av = self._classificar(1750, setembro=[1599, 1599], outubro=[1599, 1599, 1999, 1999])
        assert av.status in ("misto", "acima")


class TestTextoComentario:
    def test_bom_com_as_duas_referencias(self):
        av = hp.classificar("meli:p:MLB1", 127900, "2026-10", {
            "2026-09": _agregado(1399, 1399, 1399, 1399, 1399, 1399),
            "2026-10": _agregado(1289, 1289, 1289),
        })
        texto = hp.texto_comentario(av)
        assert texto.startswith("✅ <b>Bom momento pra comprar!</b>")
        assert "💰 Agora: R$ 1.279" in texto
        assert "📉 8% abaixo da média de setembro (R$ 1.399 em 6 promoções)" in texto
        assert "➖ Em linha com a média de outubro até aqui (R$ 1.289 em 3 promoções)" in texto
        assert hp.OBSERVACAO in texto

    def test_excelente_com_menor_preco(self):
        av = hp.classificar("meli:p:MLB1", 116900, "2026-10", {"2026-09": _agregado(1499, 1499, 1450, 1520)})
        texto = hp.texto_comentario(av)
        assert texto.startswith("🔥 <b>Preço excelente!</b>")
        assert "🏆 Menor preço desde o início de setembro." in texto
        assert hp.reacao(av) == "🔥"

    def test_sem_comentario_quando_nao_e_bom(self):
        av = hp.classificar("meli:p:MLB1", 100000, "2026-10", {"2026-09": _agregado(1000, 1000)})
        assert hp.texto_comentario(av) is None
        assert hp.texto_comentario(None) is None


# ----------------------------------------------------------------------
# Banco agregado
# ----------------------------------------------------------------------
class TestRegistrarEAvaliar:
    CHAVE = "kabum:1048336"

    def _registrar(self, reais, dia, mes=9, hora=15):
        return hp.registrar_e_avaliar(self.CHAVE, reais * 100, _utc(2026, mes, dia, hora), titulo="Ryzen 5 7600X3D")

    def test_media_do_mes_passado_com_o_estado_de_antes(self):
        self._registrar(1600, 5)
        self._registrar(1700, 12)
        av = self._registrar(1500, 3, mes=10)
        assert av.status == "bom"
        assert av.referencias[0].media == 165000      # o próprio post não entra na conta
        assert av.registrado is True

    def test_espelho_do_outro_canal_conta_uma_vez(self):
        self._registrar(1600, 5)
        segundo = self._registrar(1600, 5, hora=16)
        assert segundo.registrado is False
        self._registrar(1600, 6)
        av = self._registrar(1500, 3, mes=10)
        assert av.referencias[0].n == 2

    def test_preco_fora_da_curva_nao_contamina_a_media(self):
        self._registrar(1600, 5)
        self._registrar(1600, 6)
        av = self._registrar(300, 7)                  # leitura errada ou outro produto
        assert av.registrado is False
        assert self._registrar(1600, 3, mes=10).referencias[0].media == 160000

    def test_um_comentario_por_produto_por_dia(self):
        self._registrar(1600, 5)
        self._registrar(1600, 6)
        primeiro = self._registrar(1500, 3, mes=10)
        repetido = self._registrar(1510, 3, mes=10, hora=18)
        mais_barato = self._registrar(1400, 3, mes=10, hora=20)
        assert hp.texto_comentario(primeiro) is not None
        assert repetido.ja_comentado is True and hp.texto_comentario(repetido) is None
        assert mais_barato.ja_comentado is False and hp.texto_comentario(mais_barato) is not None

    def test_poda_meses_antigos(self, tmp_path):
        caminho = str(tmp_path / "poda.db")
        hp.registrar_e_avaliar(self.CHAVE, 100000, _utc(2025, 1, 10), caminho=caminho)
        hp.registrar_e_avaliar(self.CHAVE, 100000, _utc(2026, 10, 10), caminho=caminho)
        import sqlite3
        with sqlite3.connect(caminho) as con:
            meses = [m for (m,) in con.execute("SELECT mes FROM precos_mes ORDER BY mes")]
        assert meses == ["2026-10"]

    def test_duas_threads_gravando(self):
        erros = []

        def gravar(dia):
            try:
                for hora in range(10):
                    hp.registrar_e_avaliar(self.CHAVE, (1600 + dia) * 100, _utc(2026, 9, dia, hora + 3))
            except Exception as e:      # pragma: no cover - só aparece se falhar
                erros.append(e)
        threads = [threading.Thread(target=gravar, args=(d,)) for d in (5, 6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert erros == []

    def test_avaliar_oferta_completo(self):
        texto = "Processador AMD Ryzen 5 7600X3D\n💰POR: R$ 1601\nLINK: https://tidd.ly/4ydoEQV"
        url = "https://www.kabum.com.br/produto/1048336/processador-amd-ryzen-5-7600x3d-4-7ghz"
        av, motivo = hp.avaliar_oferta(texto, [url], _utc(2026, 10, 9))
        assert (motivo, av.chave, av.centavos, av.status) == ("ok", "kabum:1048336", 160100, "sem_historico")

    def test_parcelado_da_kabum_nao_entra(self):
        texto = "Processador AMD Ryzen 5 7600X3D\n💵 R$ 1779 em 10x sem juros"
        url = "https://www.kabum.com.br/produto/1048336/processador-amd-ryzen-5-7600x3d"
        assert hp.avaliar_oferta(texto, [url]) == (None, "so_cartao")

    def test_resolver_so_e_chamado_com_preco(self):
        chamadas = []

        def resolver():
            chamadas.append(1)
            return []
        hp.avaliar_oferta("NOVO CUPOM SHOPEE\nR$20 OFF em R$60", [], resolver=resolver)
        assert chamadas == []
        hp.avaliar_oferta("Mouse Gamer\n💵 R$ 99", [], resolver=resolver)
        assert chamadas == [1]


def test_modulo_nao_puxa_telethon_nem_observable():
    """O importador roda na VPS em outro processo: importar o observable abriria
    a sessão do Telethon (AuthKeyDuplicated derruba a produção)."""
    codigo = ("import sys, historico_precos, importar_journal; "
              "print(sorted(m for m in ('telethon', 'observable', 'dotenv') if m in sys.modules))")
    saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                           cwd=hp.BASE_DIR, check=True).stdout.strip()
    assert saida == "[]"
