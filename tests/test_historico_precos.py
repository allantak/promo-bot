"""
Testes do histórico de preços (historico_precos.py): leitura do preço nos
formatos reais dos canais, identidade do produto, comparação com a média do
mês passado/atual e o banco agregado. Sem rede; o banco vai para tmp_path
(fixture historico_isolado do conftest).
"""
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from dataclasses import replace
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

    def test_pela_media_do_modelo(self):
        av = replace(hp.classificar("meli:u:MLBU1", 243999, "2026-10",
                                    {"2026-10": _agregado(2609, 2645, 2617, 2663, 2559, 2699)},
                                    hp.MIN_PROMOCOES_MODELO), modelo="RTX 5060")
        texto = hp.texto_comentario(av)
        assert texto.startswith("✅ <b>Bom momento pra comprar!</b>")
        assert "📉 7% abaixo da média das RTX 5060 de outubro até aqui (R$ 2.632 em 6 promoções)" in texto
        assert "🏆 Menor preço de RTX 5060 de outubro até aqui." in texto
        assert hp.OBSERVACAO_MODELO.format(modelo="RTX 5060") in texto
        assert hp.OBSERVACAO not in texto
        assert "pela média das RTX 5060" in hp.descrever(av)


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


# ----------------------------------------------------------------------
# Modelo: a mesma placa em vários anúncios e lojas
# ----------------------------------------------------------------------
class TestModeloDoTexto:
    @pytest.mark.parametrize("texto, modelo", [
        # Títulos reais dos canais (set–out/2026)
        ("Placa De Vídeo Msi Rtx 5060 8g Shadow 2x Oc Gddr7 128 Bits", "RTX 5060"),
        ("Placa De Vídeo Nvidia Msi Geforce 5060 8GB shadow 2x oc Edition", "RTX 5060"),
        ("RTX 5060 Placa de Vídeo INNO3D GeForce RTX5060 Twin X2 8GB GDDR7 128-bit", "RTX 5060"),
        ("ZOTAC GAMING GEFORCE RTX 5060 8GB 128BITS GDDR7 TWIN EDGE OC ZT-B50600H-10M", "RTX 5060"),
        ("Placa de Video Palit GeForce RTX 5060 8GB INFINITY 2 OC GDDR7 128bits - NE75060V19P1-GB2063L", "RTX 5060"),
        ("Placa De Vídeo Gigabyte Rtx 5060 Eagle Max Oc\n- 8GB Gddr7", "RTX 5060"),
        ("NVIDIA RTX 5060 PALIT INFINITY", "RTX 5060"),
        ("Placa de Vídeo GeForce Gigabyte 5060, 8GB GDDR7, 128 bits.", "RTX 5060"),
        ("Placa de Vídeo RTX 5060 8GB para PC Gamer", "RTX 5060"),
        ("Placa De Vídeo Pcyes Rtx 5060 Ti 16gb Gddr7 128 Bits Preta", "RTX 5060 Ti 16GB"),
        ("Placa De Video Asus RTX 5060 Ti Dual O8G Nvidia Geforce 8GB Gddr7 Dlss Ray Tracing", "RTX 5060 Ti 8GB"),
        ("Placa de Video GeForce NVIDIA PALIT RTX5060TI 8GB DUAL GDDR7 NE7506T019P1-GB2062D", "RTX 5060 Ti 8GB"),
        ("Placa de Vídeo GALAX - GeForce RTX 3080 Serious Gaming, 10GB, LHR, RGB", "RTX 3080 10GB"),
        ("Placa De Vídeo Superframe Nvidia Geforce Rtx 3070 Epic 8 Gb", "RTX 3070"),
        ("Placa de Vídeo ASRock Radeon RX 7600 8GB Challenger OC GDDR6 HDMI 2.1", "RX 7600"),
        ("Placa de Vídeo Sapphire Pulse AMD Radeon RX 9060 XT 16GB GDDR6", "RX 9060 XT 16GB"),
        ("Placa de Vídeo XFX Speedster Radeon RX 7900 XTX 24GB", "RX 7900 XTX"),
        ("Placa de Vídeo Intel Arc B580 Challenger 12GB OC", "Arc B580"),
        ("Placa de Vídeo Gainward GeForce GTX 1660 Super Ghost 6GB", "GTX 1660 Super"),
    ])
    def test_placas(self, texto, modelo):
        assert hp.modelo_do_texto(texto) == modelo

    @pytest.mark.parametrize("texto", [
        "Notebook ASUS TUF Gaming F16 Intel i5 NVIDIA RTX™ 5060 16GB 512GB SSD Windows 11",
        "Notebook Gamer ROG Strix G16\n- 16GB\n- 512GB\n- RTX5060",
        "PC Gamer Completo Ryzen 5 5600 RTX 5060 8GB 16GB RAM",
        "Kit Upgrade Ryzen 5 5600 + Placa de Vídeo RTX 5060",
        "Suporte para Placa de Vídeo RTX 5090",
        "Processador AMD Ryzen 5 7600, 3.8GHz, AM5",               # 7600 de processador, não RX
        "Processador AMD Ryzen 5 8600G com Radeon 760M",
        "Placa de Vídeo RTX 5060 Ti",                             # 8 ou 16 GB? o texto não diz
        "Placa de Vídeo RTX 5060 Ti 8GB ou 16GB",
        "VERSÂO TI COM O VALOR DA 5060 NORMAL\nPlaca De Video Pny Rtx5060ti 8GB",   # dois chips
        "Placa de Vídeo Gigabyte GV-N5060AERO OC-8GD",            # só o código do fabricante
        "Placa de Vídeo RTX 5070 Super 18GB",                     # chip fora da tabela
        "Placa de Vídeo RTX 5060 16GB",                           # a RTX 5060 só tem 8 GB
        "Monitor Gamer LG UltraGear 27 180Hz",
        "",
    ])
    def test_sem_modelo(self, texto):
        assert hp.modelo_do_texto(texto) is None


class TestAgregarModelo:
    def test_soma_os_anuncios_de_cada_mes(self):
        linhas = [("2026-10", 2, 500000, 240000, 260000), ("2026-10", 1, 255000, 255000, 255000),
                  ("2026-09", 3, 690000, 220000, 240000)]
        assert hp.agregar_modelo(linhas) == {"2026-10": (3, 755000, 240000, 260000),
                                             "2026-09": (3, 690000, 220000, 240000)}

    def test_anuncio_com_preco_absurdo_fica_de_fora(self):
        # Casos reais com o nome da placa: "R$ 97.899,00 NO PIX" e "R$ 234" (parcela).
        linhas = [("2026-10", 2, 500000, 240000, 260000), ("2026-10", 1, 255000, 255000, 255000),
                  ("2026-10", 1, 9789900, 9789900, 9789900), ("2026-10", 1, 23400, 23400, 23400),
                  ("2026-10", 2, 266500, 23400, 243100)]
        assert hp.agregar_modelo(linhas) == {"2026-10": (3, 755000, 240000, 260000)}


# Promoções de RTX 5060 de outros anúncios em outubro: média R$ 2.632.
OUTRAS_5060 = [
    ("amz:B0GQ17JZFJ", 2609, 8), ("kabum:1", 2645, 9), ("meli:p:MLB52175612", 2617, 10),
    ("meli:p:MLB50869989", 2663, 11), ("shopee:1.2", 2559, 12), ("kabum:2", 2699, 14),
]
POST_MSI = ("Placa De Vídeo Msi Rtx 5060 8g Shadow 2x Oc Gddr7 128 Bits\n\nPOR: R$2.439,99\n"
            "Cupom: RESGATEALLSITE + Cupom da loja\n\nhttps://meli.la/17jqwgq\n\n(anúncio)")
URL_MSI_UP = ("https://www.mercadolivre.com.br/placa-de-video-msi-rtx-5060-8g-shadow-2x-oc-gddr7-128-bits"
              "/up/MLBU4899388815")


def _semear_5060(promocoes=OUTRAS_5060, caminho=None):
    for chave, reais, dia in promocoes:
        hp.registrar_e_avaliar(chave, reais * 100, _utc(2026, 10, dia), titulo="Placa de Vídeo RTX 5060",
                               caminho=caminho, modelo="RTX 5060")


class TestMediaDoModelo:
    def test_anuncio_novo_compara_com_a_media_do_modelo(self):
        # Caso real (15/10, 01:23): a MSI saiu num /up/ do ML que nunca tinha
        # aparecido e o bot ficou calado ("sem_historico").
        _semear_5060()
        av, motivo = hp.avaliar_oferta(POST_MSI, [URL_MSI_UP], _utc(2026, 10, 15, 4))
        assert (motivo, av.chave, av.modelo, av.status) == ("ok", "meli:u:MLBU4899388815", "RTX 5060", "bom")
        assert av.menor_desde == "2026-10"
        comentario = hp.texto_comentario(av)
        assert "📉 7% abaixo da média das RTX 5060 de outubro até aqui (R$ 2.632 em 6 promoções)" in comentario
        assert "🏆 Menor preço de RTX 5060 de outubro até aqui." in comentario

    def test_o_modelo_vem_do_titulo_e_nao_do_que_vem_depois_do_preco(self):
        texto = ("⚠️PERGUNTA\n\nPlaca De Vídeo Superframe Nvidia Geforce Rtx 3070 Epic 8 Gb\n\nPOR: 2196\n"
                 "CUPOM: VALEDESCONTO\n\nhttps://meli.la/2zYj93V\n\nA 3070 tem desempenho parecido com a 5060")
        url = "https://www.mercadolivre.com.br/placa-de-video-superframe-rtx-3070-epic-8gb/p/MLB123"
        assert hp.avaliar_oferta(texto, [url], _utc(2026, 10, 2))[1] == "ok"
        with closing(sqlite3.connect(hp.CAMINHO_BANCO)) as con:
            assert con.execute("SELECT modelo FROM produtos WHERE chave = 'meli:p:MLB123'").fetchone() == ("RTX 3070",)

    def test_anuncio_com_historico_proprio_ignora_o_modelo(self):
        _semear_5060()
        _semear_5060([("meli:u:MLBU4899388815", 2400, 3), ("meli:u:MLBU4899388815", 2400, 4)])
        av, _ = hp.avaliar_oferta(POST_MSI, [URL_MSI_UP], _utc(2026, 10, 15, 4))
        assert (av.modelo, av.status) == (None, "na_media")       # 2% acima da própria média

    def test_modelo_com_poucas_promocoes_nao_vira_referencia(self):
        _semear_5060(OUTRAS_5060[:hp.MIN_PROMOCOES_MODELO - 1])
        av, _ = hp.avaliar_oferta(POST_MSI, [URL_MSI_UP], _utc(2026, 10, 15, 4))
        assert (av.modelo, av.status) == (None, "sem_historico")
        assert hp.texto_comentario(av) is None

    def test_preco_absurdo_de_anuncio_novo_nao_entra_no_historico(self):
        _semear_5060()
        digitado_errado = hp.registrar_e_avaliar("shopee:9.9", 9789900, _utc(2026, 10, 15), modelo="RTX 5060")
        acessorio = hp.registrar_e_avaliar("shopee:8.8", 3000, _utc(2026, 10, 15), modelo="RTX 5060")
        assert (digitado_errado.status, digitado_errado.registrado) == ("acima", False)
        assert (acessorio.status, acessorio.registrado) == ("suspeito", False)
        assert hp.texto_comentario(acessorio) is None

    def test_banco_antigo_ganha_o_modelo_pelo_titulo_e_ja_serve(self, tmp_path):
        # Banco da versão anterior (sem a coluna modelo), com histórico de verdade.
        caminho = str(tmp_path / "antigo.db")
        with closing(sqlite3.connect(caminho)) as con:
            con.executescript("""
                CREATE TABLE precos_mes (chave TEXT NOT NULL, mes TEXT NOT NULL, n INTEGER NOT NULL,
                    soma INTEGER NOT NULL, minimo INTEGER NOT NULL, maximo INTEGER NOT NULL,
                    PRIMARY KEY (chave, mes)) WITHOUT ROWID;
                CREATE TABLE produtos (chave TEXT PRIMARY KEY, titulo TEXT, ultimo_dia TEXT,
                    ultimo_centavos INTEGER, comentado_dia TEXT, comentado_centavos INTEGER) WITHOUT ROWID;
            """)
            con.executemany("INSERT INTO produtos (chave, titulo) VALUES (?, ?)", [
                ("meli:p:MLB51032833", "Placa De Vídeo Nvidia Msi Geforce 5060 8GB shadow 2x oc Edition"),
                ("amz:B0GQ17JZFJ", "ZOTAC GAMING GEFORCE RTX 5060 8GB 128BITS GDDR7 TWIN EDGE OC"),
                ("kabum:1048336", "Processador AMD Ryzen 5 7600X3D"),
            ])
            con.executemany("INSERT INTO precos_mes VALUES (?, '2026-10', ?, ?, ?, ?)", [
                ("meli:p:MLB51032833", 4, 1072600, 255900, 299900),
                ("amz:B0GQ17JZFJ", 3, 783100, 255900, 265900),
                ("kabum:1048336", 2, 320000, 160000, 160000),
            ])
            con.commit()

        av, _ = hp.avaliar_oferta(POST_MSI, [URL_MSI_UP], _utc(2026, 10, 15, 4), caminho=caminho)

        assert (av.modelo, av.status) == ("RTX 5060", "bom")    # média R$ 2.651 de 7 promoções: 7% abaixo
        with closing(sqlite3.connect(caminho)) as con:
            assert dict(con.execute("SELECT chave, modelo FROM produtos")) == {
                "meli:p:MLB51032833": "RTX 5060", "amz:B0GQ17JZFJ": "RTX 5060", "kabum:1048336": None,
                "meli:u:MLBU4899388815": "RTX 5060"}
            assert con.execute("PRAGMA user_version").fetchone()[0] == hp.VERSAO_MODELOS

    def test_cli_do_modelo(self, capsys):
        _semear_5060()
        hp.main(["modelo"])
        assert "RTX 5060" in capsys.readouterr().out
        hp.main(["modelo", "RTX 5060"])
        saida = capsys.readouterr().out
        assert "RTX 5060: 6 anúncios" in saida
        assert "2026-10: média R$ 2.632 em 6 promoções (menor R$ 2.559, maior R$ 2.699)" in saida


def test_modulo_nao_puxa_telethon_nem_observable():
    """O importador roda na VPS em outro processo: importar o observable abriria
    a sessão do Telethon (AuthKeyDuplicated derruba a produção)."""
    codigo = ("import sys, historico_precos, importar_journal; "
              "print(sorted(m for m in ('telethon', 'observable', 'dotenv') if m in sys.modules))")
    saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                           cwd=hp.BASE_DIR, check=True).stdout.strip()
    assert saida == "[]"
