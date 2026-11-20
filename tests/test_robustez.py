"""
Testes de robustez: deduplicação, estado persistente, filtro de idade
(catch_up), vigias, handler fora do event loop e timeouts. Sem rede e sem
Telegram (o client é usado só com métodos substituídos por fakes).
"""
import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
from PIL import Image
from telethon import errors, functions, types

import historico_precos
import observable


class _Reinicio(Exception):
    """Sentinela: substitui o os._exit de reiniciar_processo nos testes."""


# ----------------------------------------------------------------------
# Deduplicação
# ----------------------------------------------------------------------
class TestDedup:
    def test_cache_comporta_mais_de_10_titulos(self):
        assert observable.cache_links.maxsize >= 500
        assert observable.cache_links.ttl == 300

    def test_liberar_dedup_permite_nova_tentativa(self):
        msg = "SSD Kingston NV3 1TB\nhttps://meli.la/abc"
        assert observable.ja_foi_enviado(msg) is False
        observable.liberar_dedup(msg)
        assert observable.ja_foi_enviado(msg) is False   # liberado: não é duplicata

    def test_liberar_dedup_de_titulo_inexistente_nao_quebra(self):
        observable.liberar_dedup("Nunca visto\nhttps://meli.la/x")


# ----------------------------------------------------------------------
# Estado persistente: posts já tratados sobrevivem a restarts
# ----------------------------------------------------------------------
class TestEstadoPersistente:
    def test_marcar_e_consultar(self):
        assert observable.ja_processado(-100, 10) is False
        observable.marcar_processado(-100, 10)
        assert observable.ja_processado(-100, 10) is True
        assert observable.ja_processado(-100, 9) is True     # mais antigo também
        assert observable.ja_processado(-100, 11) is False
        assert observable.ja_processado(-200, 5) is False    # outro canal

    def test_sobrevive_a_um_novo_processo(self, monkeypatch):
        observable.marcar_processado(-100, 42)
        # Simula o restart: memória vazia, só o arquivo em disco.
        monkeypatch.setattr(observable, "_ultimo_processado", None)
        assert observable.ja_processado(-100, 42) is True

    def test_arquivo_corrompido_nao_derruba_o_bot(self):
        with open(observable.CAMINHO_ESTADO, "w", encoding="utf-8") as f:
            f.write("{isso não é json")
        assert observable.ja_processado(-100, 1) is False
        observable.marcar_processado(-100, 1)                  # regrava por cima
        with open(observable.CAMINHO_ESTADO, encoding="utf-8") as f:
            assert json.load(f)["ultimo_processado"] == {"-100": 1}


# ----------------------------------------------------------------------
# Avisos ao admin com cooldown em disco
# ----------------------------------------------------------------------
class TestAvisoComCooldown:
    def test_segundo_aviso_do_mesmo_tipo_e_silenciado(self, monkeypatch, fake_response):
        enviados = []

        def fake_post(url, **kwargs):
            enviados.append(kwargs["json"]["text"])
            return fake_response(status_code=200)
        monkeypatch.setattr(observable.requests, "post", fake_post)

        observable.avisar_admin_com_cooldown("reinicio", "primeiro")
        observable.avisar_admin_com_cooldown("reinicio", "segundo")
        observable.avisar_admin_com_cooldown("sessao_invalida", "outro tipo")
        assert enviados == ["primeiro", "outro tipo"]

    def test_falha_no_envio_nao_conta_para_o_cooldown(self, monkeypatch, fake_response):
        respostas = iter([fake_response(status_code=500, text="erro"), fake_response(status_code=200)])
        enviados = []

        def fake_post(url, **kwargs):
            enviados.append(kwargs["json"]["text"])
            return next(respostas)
        monkeypatch.setattr(observable.requests, "post", fake_post)

        observable.avisar_admin_com_cooldown("reinicio", "a")
        observable.avisar_admin_com_cooldown("reinicio", "b")
        assert enviados == ["a", "b"]


# ----------------------------------------------------------------------
# Filtro de idade (posts recuperados pelo catch_up depois de uma parada)
# ----------------------------------------------------------------------
class TestPostAntigo:
    AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

    def test_post_recente_passa(self):
        assert observable.post_antigo(self.AGORA - timedelta(minutes=5), self.AGORA) is False

    def test_post_velho_e_descartado(self):
        assert observable.post_antigo(self.AGORA - timedelta(hours=2), self.AGORA) is True

    def test_limite(self):
        limite = observable.IDADE_MAXIMA_POST
        assert observable.post_antigo(self.AGORA - limite, self.AGORA) is False
        assert observable.post_antigo(self.AGORA - limite - timedelta(seconds=1), self.AGORA) is True


# ----------------------------------------------------------------------
# Vigia de updates
# ----------------------------------------------------------------------
def _msg(chat_id, msg_id, idade_s, action=None):
    return SimpleNamespace(chat_id=chat_id, id=msg_id, action=action,
                           date=datetime.now(timezone.utc) - timedelta(seconds=idade_s))


class _ClienteFalso:
    """Substitui get_input_entity/get_messages do client por respostas fixas."""

    def __init__(self, monkeypatch, mensagens_por_canal):
        self.mensagens = mensagens_por_canal
        monkeypatch.setattr(observable.client, "get_input_entity", self.get_input_entity)
        monkeypatch.setattr(observable.client, "get_messages", self.get_messages)

    async def get_input_entity(self, canal):
        return canal

    async def get_messages(self, canal, limit=5):
        resultado = self.mensagens[canal]
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


class TestVigia:
    A, B = observable.CANAIS_ALVO[0], observable.CANAIS_ALVO[1]
    TOL = observable.VIGIA_TOLERANCIA

    def test_registrar_visto_guarda_o_maior_id(self):
        observable.registrar_visto(-100, 10)
        observable.registrar_visto(-100, 7)     # catch_up pode entregar fora de ordem
        assert observable._ultimo_id_visto[-100] == 10

    def test_canal_atrasado(self):
        agora = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        velho = agora - timedelta(seconds=self.TOL + 1)
        recente = agora - timedelta(seconds=self.TOL - 1)
        assert observable.canal_atrasado(50, 50, velho, agora) is False    # já visto
        assert observable.canal_atrasado(50, 51, recente, agora) is False  # ainda no prazo
        assert observable.canal_atrasado(50, 51, velho, agora) is True

    def test_primeira_rodada_vira_linha_de_base(self, monkeypatch):
        _ClienteFalso(monkeypatch, {self.A: [_msg(-1, 100, 3600)], self.B: [_msg(-2, 200, 3600)]})
        assert asyncio.run(observable.checar_canais({})) is None
        assert observable._ultimo_id_visto == {-1: 100, -2: 200}

    def test_post_que_nao_chegou_ao_handler_e_detectado(self, monkeypatch):
        observable.registrar_visto(-1, 100)
        observable.registrar_visto(-2, 200)
        _ClienteFalso(monkeypatch, {self.A: [_msg(-1, 101, self.TOL + 60)], self.B: [_msg(-2, 200, 10)]})
        problema = asyncio.run(observable.checar_canais({}))
        assert problema and "101" in problema

    def test_mensagem_de_servico_e_pulada(self, monkeypatch):
        observable.registrar_visto(-1, 100)
        observable.registrar_visto(-2, 200)
        fixado = _msg(-1, 105, self.TOL + 60, action=object())   # "post fixado"
        _ClienteFalso(monkeypatch, {self.A: [fixado, _msg(-1, 100, 3600)], self.B: [_msg(-2, 200, 10)]})
        assert asyncio.run(observable.checar_canais({})) is None

    def test_erro_do_canal_avisa_e_nao_conta_como_surdo(self, monkeypatch):
        avisos = []
        monkeypatch.setattr(observable, "avisar_admin_com_cooldown", lambda tipo, texto: avisos.append(tipo))
        observable.registrar_visto(-2, 200)
        _ClienteFalso(monkeypatch, {self.A: ValueError("canal privado"), self.B: [_msg(-2, 200, 10)]})
        assert asyncio.run(observable.checar_canais({})) is None
        assert avisos == [f"canal:{self.A}"]

    def test_sem_resposta_do_telegram_conta_como_problema(self, monkeypatch):
        _ClienteFalso(monkeypatch, {self.A: ConnectionError("caiu"), self.B: []})
        assert "sem resposta" in asyncio.run(observable.checar_canais({}))

    def test_reinicia_depois_de_duas_rodadas_ruins(self, monkeypatch):
        monkeypatch.setattr(observable, "VIGIA_INTERVALO", 0)
        rodadas = iter(["surdo 1", None, "surdo 2", "surdo 3"])

        async def checar(entidades):
            return next(rodadas)
        monkeypatch.setattr(observable, "checar_canais", checar)

        def reiniciar(motivo):
            raise _Reinicio(motivo)
        monkeypatch.setattr(observable, "reiniciar_processo", reiniciar)

        with pytest.raises(_Reinicio) as exc:
            asyncio.run(observable.vigiar_updates())
        # "surdo 1" não basta (a rodada seguinte zerou a contagem); 2 e 3 seguidas sim.
        assert "surdo 3" in str(exc.value)


# ----------------------------------------------------------------------
# Handler: fila, prazo, estado e trabalho fora do event loop
# ----------------------------------------------------------------------
def _evento(texto, arquivo=None, chat_id=-100123, msg_id=1, idade_s=5):
    async def get_chat():
        return SimpleNamespace(title="Canal Teste")
    return SimpleNamespace(
        raw_text=texto, get_chat=get_chat, chat_id=chat_id, id=msg_id,
        message=SimpleNamespace(file=arquivo, date=datetime.now(timezone.utc) - timedelta(seconds=idade_s)),
    )


class TestProcessarPromocao:
    TEXTO = "Mouse Logitech G305\n💰 R$ 199\nhttps://meli.la/abc"

    def test_conversao_e_envio_rodam_fora_do_event_loop(self, monkeypatch):
        threads = []

        def converter(t):
            threads.append(threading.current_thread())
            return t, True
        monkeypatch.setattr(observable, "substituir_links_no_texto", converter)
        monkeypatch.setattr(observable, "publicar", lambda *a: threads.append(threading.current_thread()))

        asyncio.run(observable.processar_promocao(_evento(self.TEXTO)))
        assert len(threads) == 2
        assert all(t is not threading.main_thread() for t in threads)

    def test_falha_na_conversao_libera_o_dedup(self, monkeypatch):
        monkeypatch.setattr(observable, "substituir_links_no_texto", lambda t: (t, False))
        publicados = []
        monkeypatch.setattr(observable, "publicar", lambda *a: publicados.append(a))

        asyncio.run(observable.processar_promocao(_evento(self.TEXTO)))

        assert publicados == []
        # O mesmo produto vindo do outro canal ainda pode sair.
        assert observable.ja_foi_enviado(self.TEXTO) is False

    def test_sucesso_publica_texto_convertido(self, monkeypatch):
        monkeypatch.setattr(observable, "substituir_links_no_texto",
                            lambda t: (t.replace("meli.la/abc", "meli.la/MEU"), True))
        publicados = []
        monkeypatch.setattr(observable, "publicar", lambda *a: publicados.append(a))

        asyncio.run(observable.processar_promocao(_evento(self.TEXTO)))

        assert len(publicados) == 1
        texto, caminho_imagem = publicados[0][:2]
        assert "meli.la/MEU" in texto
        assert caminho_imagem is None
        assert observable.ja_foi_enviado(self.TEXTO) is True   # marcado como enviado

    def test_foto_e_baixada_e_vai_para_publicar(self, monkeypatch, tmp_path):
        monkeypatch.setattr(observable, "substituir_links_no_texto", lambda t: (t, True))
        publicados = []
        monkeypatch.setattr(observable, "publicar", lambda *a: publicados.append(a))
        baixada = str(tmp_path / "foto.jpg")

        async def baixar(msg, file=None):
            return baixada
        monkeypatch.setattr(observable.client, "download_media", baixar)

        foto = SimpleNamespace(mime_type="image/jpeg", size=150_000)
        asyncio.run(observable.processar_promocao(_evento(self.TEXTO, arquivo=foto)))
        assert publicados[0][1] == baixada

    @pytest.mark.parametrize("arquivo", [
        SimpleNamespace(mime_type="video/mp4", size=5_000_000),
        SimpleNamespace(mime_type="image/png", size=50 * 1024 * 1024),   # imagem gigante
    ])
    def test_video_ou_imagem_gigante_nao_sao_baixados(self, monkeypatch, arquivo):
        monkeypatch.setattr(observable, "substituir_links_no_texto", lambda t: (t, True))
        publicados = []
        monkeypatch.setattr(observable, "publicar", lambda *a: publicados.append(a))
        downloads = []

        async def baixar(*a, **k):
            downloads.append(a)
        monkeypatch.setattr(observable.client, "download_media", baixar)

        asyncio.run(observable.processar_promocao(_evento(self.TEXTO, arquivo=arquivo)))
        assert downloads == []
        assert publicados[0][1] is None          # sai só com texto

    def test_download_que_trava_nao_segura_o_post(self, monkeypatch):
        monkeypatch.setattr(observable, "substituir_links_no_texto", lambda t: (t, True))
        publicados = []
        monkeypatch.setattr(observable, "publicar", lambda *a: publicados.append(a))

        async def baixar_para_sempre(*a, **k):
            await asyncio.sleep(3600)
        monkeypatch.setattr(observable.client, "download_media", baixar_para_sempre)

        real_wait_for = asyncio.wait_for

        async def wait_for_rapido(aw, timeout):   # 60 s de prazo viram 0,05 s
            return await real_wait_for(aw, min(timeout, 0.05))
        monkeypatch.setattr(observable.asyncio, "wait_for", wait_for_rapido)

        foto = SimpleNamespace(mime_type="image/jpeg", size=150_000)
        asyncio.run(observable.processar_promocao(_evento(self.TEXTO, arquivo=foto)))
        assert publicados and publicados[0][1] is None   # desistiu da imagem e postou o texto


# ----------------------------------------------------------------------
# Post de origem sem foto. Caso real (19/11/2026): o OQMDV mandou 10 placas
# da KaBuM seguidas só com texto, e as nossas saíram sem foto também.
# ----------------------------------------------------------------------
URL_PLACA = ("https://www.kabum.com.br/produto/1078270/placa-de-video-nvidia-gainward-rtx-5070-12gb-python-iii"
             "-gddr7-192bits-gainward-ne75070019k9-gb2050t")
TEXTO_PLACA = ("Placa de Vídeo Nvidia Gainward RTX 5070 12GB PYTHON III GDDR7 192BITS GAINWARD NE75070019K9-GB2050T\n"
               "💰 POR: R$ 5029\n🎟️ Cupom: PEGAOZ4F4D1NH0\n🛒 https://tidd.ly/4BVAty9\nANUNCIO")
CHAVE_PLACA = "kabum:1078270"


def _foto_telethon():
    return types.Photo(id=1, access_hash=2, file_reference=b"", date=datetime.now(timezone.utc),
                       sizes=[types.PhotoSize(type="x", w=200, h=200, size=9000)], dc_id=1)


def _pagina_com_foto(foto):
    return types.WebPage(id=1, url=URL_PLACA, display_url="kabum.com.br", hash=0, photo=foto)


def _previa(pagina):
    """Resposta do messages.getWebPagePreview."""
    return types.messages.WebPagePreview(media=types.MessageMediaWebPage(webpage=pagina), chats=[], users=[])


class _TelethonDePrevias:
    """Faz o papel do client do Telethon: responde, em ordem, aos pedidos de
    prévia de link e "baixa" a foto para `baixada`."""

    def __init__(self, respostas=(), baixada=None):
        self.respostas = list(respostas)
        self.pedidos = []
        self.downloads = []
        self.baixada = baixada

    async def __call__(self, pedido):
        self.pedidos.append(pedido)
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    async def download_media(self, midia, file=None):
        self.downloads.append(midia)
        return self.baixada


class TestPostSemFoto:
    def _processar(self, monkeypatch, telethon, texto=TEXTO_PLACA,
                   urls=(URL_PLACA + "?awc=17729_1795130706&utm_source=AWIN",)):
        def converter(t):
            for url in urls:
                observable._anotar_url_produto(url)          # como o converter_link_kabum faz
            return t.replace("tidd.ly/4BVAty9", "tidd.ly/4sZG5mU"), True
        monkeypatch.setattr(observable, "substituir_links_no_texto", converter)
        monkeypatch.setattr(observable, "client", telethon)
        publicados = []
        monkeypatch.setattr(observable, "publicar", lambda *a: publicados.append(a) or 555)
        asyncio.run(observable.processar_promocao(_evento(texto)))
        return publicados

    def test_usa_a_ultima_foto_do_produto_no_canal(self, monkeypatch):
        historico_precos.guardar_foto(CHAVE_PLACA, "FOTO_DO_CANAL")
        telethon = _TelethonDePrevias()

        texto, caminho_imagem, chave, foto_salva = self._processar(monkeypatch, telethon)[0]

        assert "tidd.ly/4sZG5mU" in texto
        assert (caminho_imagem, chave, foto_salva) == (None, CHAVE_PLACA, "FOTO_DO_CANAL")
        assert telethon.pedidos == []          # nem pede a prévia: a foto do canal é maior

    def test_sem_foto_guardada_usa_a_previa_da_pagina_da_kabum(self, monkeypatch, tmp_path):
        foto = _foto_telethon()
        baixada = str(tmp_path / "previa.jpg")
        telethon = _TelethonDePrevias([_previa(_pagina_com_foto(foto))], baixada)

        publicados = self._processar(monkeypatch, telethon)

        pedido, = telethon.pedidos
        assert isinstance(pedido, functions.messages.GetWebPagePreviewRequest)
        assert pedido.message == URL_PLACA     # a página do produto, sem a query de afiliado
        assert telethon.downloads == [foto]
        assert publicados[0][1:] == (baixada, CHAVE_PLACA, None)

    def test_previa_ainda_sendo_montada_espera_e_pede_de_novo(self, monkeypatch, tmp_path):
        monkeypatch.setattr(observable, "ESPERAS_PREVIA", (0, 0))
        baixada = str(tmp_path / "previa.jpg")
        telethon = _TelethonDePrevias([
            _previa(types.WebPagePending(id=1, date=None)),
            types.messages.WebPage(webpage=_pagina_com_foto(_foto_telethon()), chats=[], users=[]),
        ], baixada)

        publicados = self._processar(monkeypatch, telethon)

        assert [type(p).__name__ for p in telethon.pedidos] == ["GetWebPagePreviewRequest", "GetWebPageRequest"]
        assert publicados[0][1] == baixada

    @pytest.mark.parametrize("resposta", [
        _previa(types.WebPageEmpty(id=1)),     # o Telegram não conseguiu montar a prévia
        RuntimeError("caiu a conexão"),
    ])
    def test_sem_previa_sai_so_com_texto(self, monkeypatch, resposta):
        telethon = _TelethonDePrevias([resposta])
        publicados = self._processar(monkeypatch, telethon)
        assert publicados[0][1:] == (None, CHAVE_PLACA, None)
        assert telethon.downloads == []

    def test_floodwait_suspende_as_previas(self, monkeypatch):
        telethon = _TelethonDePrevias([errors.FloodWaitError(request=None, capture=600)])
        monkeypatch.setattr(observable, "client", telethon)
        assert asyncio.run(observable.baixar_foto_da_previa(URL_PLACA)) is None
        assert asyncio.run(observable.baixar_foto_da_previa(URL_PLACA)) is None
        assert len(telethon.pedidos) == 1      # o 2º pedido nem foi ao Telegram

    def test_loja_fora_da_lista_nao_pede_previa(self, monkeypatch):
        # A foto guardada vale para qualquer loja; a prévia, só onde foi testada
        # (em outras ela pode trazer o logo da loja em vez do produto).
        url = "https://www.mercadolivre.com.br/placa-de-video-rtx-5070-gainward-python-iii/p/MLB51032833"
        telethon = _TelethonDePrevias()
        publicados = self._processar(monkeypatch, telethon, texto="Placa de Vídeo RTX 5070 Gainward Python III\n"
                                     "💰 R$ 5029\nhttps://meli.la/abc", urls=(url,))
        assert publicados[0][1:] == (None, "meli:p:MLB51032833", None)
        assert telethon.pedidos == []

    def test_post_com_dois_produtos_nao_busca_foto(self, monkeypatch):
        historico_precos.guardar_foto(CHAVE_PLACA, "FOTO_DO_CANAL")
        telethon = _TelethonDePrevias()
        outra = "https://www.kabum.com.br/produto/1078276/placa-de-video-nvidia-msi-rtx-5070-12gb-ventus-2x"
        publicados = self._processar(monkeypatch, telethon, urls=(URL_PLACA, outra))
        assert publicados[0][1:] == (None, None, None)       # não dá para saber de qual seria a foto
        assert telethon.pedidos == []

    def test_foto_que_saiu_no_canal_vale_para_o_proximo_post_sem_foto(self, monkeypatch, fake_response, tmp_path):
        """Fluxo inteiro com o publicar de verdade: o 1º post veio com foto; o 2º,
        do mesmo produto, só com texto, e sai com a foto do 1º, sem novo upload."""
        chamadas = []

        def post(url, data=None, files=None, **kw):
            chamadas.append((url.rsplit("/", 1)[1], dict(data or {}), files))
            return fake_response(status_code=200, json_data={"ok": True, "result": {
                "message_id": 41211, "photo": [{"file_id": "PEQUENA", "width": 90, "height": 90},
                                               {"file_id": "GRANDE", "width": 1280, "height": 1280}]}})
        monkeypatch.setattr(observable.requests, "post", post)
        monkeypatch.setattr(observable, "aplicar_marca_dagua", lambda caminho: caminho)

        def converter(t):
            observable._anotar_url_produto(URL_PLACA)
            return t, True
        monkeypatch.setattr(observable, "substituir_links_no_texto", converter)
        foto_da_origem = tmp_path / "origem.jpg"
        foto_da_origem.write_bytes(b"jpg")

        async def baixar(msg, file=None):
            return str(foto_da_origem)
        monkeypatch.setattr(observable.client, "download_media", baixar)

        foto = SimpleNamespace(mime_type="image/jpeg", size=150_000)
        asyncio.run(observable.processar_promocao(_evento(TEXTO_PLACA, arquivo=foto, msg_id=1)))
        observable.cache_links.clear()         # horas depois: já saiu do dedup
        asyncio.run(observable.processar_promocao(_evento(TEXTO_PLACA, msg_id=2)))

        assert [metodo for metodo, _, _ in chamadas] == ["sendPhoto", "sendPhoto"]
        assert chamadas[0][2] is not None                    # 1º: upload da foto
        _, dados, arquivos = chamadas[1]
        assert dados["photo"] == "GRANDE" and arquivos is None
        assert historico_precos.foto_do_produto(CHAVE_PLACA) == "GRANDE"


class TestEscutarPromocoes:
    TEXTO = TestProcessarPromocao.TEXTO

    def _registrar_chamadas(self, monkeypatch):
        chamadas = []

        async def processar(event):
            chamadas.append(event.id)
        monkeypatch.setattr(observable, "processar_promocao", processar)
        return chamadas

    def test_post_antigo_nem_chega_a_processar(self, monkeypatch):
        chamadas = self._registrar_chamadas(monkeypatch)
        asyncio.run(observable.escutar_promocoes(_evento(self.TEXTO, msg_id=99, idade_s=3 * 3600)))
        assert chamadas == []
        assert observable._ultimo_id_visto[-100123] == 99   # mas conta como visto

    def test_post_reentregue_pelo_catch_up_nao_duplica(self, monkeypatch):
        chamadas = self._registrar_chamadas(monkeypatch)
        asyncio.run(observable.escutar_promocoes(_evento(self.TEXTO, msg_id=7)))
        asyncio.run(observable.escutar_promocoes(_evento(self.TEXTO, msg_id=7)))
        assert chamadas == [7]

    def test_post_que_estoura_o_prazo_libera_a_fila(self, monkeypatch):
        monkeypatch.setattr(observable, "PRAZO_POR_POST", 0.05)
        chamadas = []

        async def processar(event):
            chamadas.append(event.id)
            if event.id == 1:
                await asyncio.sleep(3600)          # trava para sempre
        monkeypatch.setattr(observable, "processar_promocao", processar)

        async def dois_posts():
            await asyncio.gather(
                observable.escutar_promocoes(_evento(self.TEXTO, msg_id=1)),
                observable.escutar_promocoes(_evento("Teclado Redragon\nhttps://meli.la/b", msg_id=2)),
            )
        asyncio.run(dois_posts())
        assert chamadas == [1, 2]                          # o 2º não ficou preso atrás do 1º
        assert observable.ja_processado(-100123, 1)        # e o 1º não volta no restart


# ----------------------------------------------------------------------
# Marca d'água: teto de tamanho para não estourar a RAM da VPS
# ----------------------------------------------------------------------
class TestMarcaDaguaTamanho:
    def test_imagem_gigante_e_reduzida(self, tmp_path):
        grande = tmp_path / "grande.jpg"
        Image.new("RGB", (6000, 4000), "white").save(grande, "JPEG")
        saida = observable.aplicar_marca_dagua(str(grande))
        assert saida != str(grande)
        with Image.open(saida) as img:
            assert max(img.size) <= observable.LADO_MAXIMO_IMAGEM


# ----------------------------------------------------------------------
# Timeouts: nenhuma chamada HTTP pode pendurar o bot para sempre
# ----------------------------------------------------------------------
class TestTimeouts:
    def _capturar_post(self, monkeypatch, fake_response, json_data=None):
        chamadas = []

        def fake_post(*args, **kwargs):
            chamadas.append(kwargs)
            return fake_response(status_code=200, json_data=json_data or {})
        monkeypatch.setattr(observable.requests, "post", fake_post)
        return chamadas

    def test_awin_tem_timeout(self, monkeypatch, fake_response):
        chamadas = self._capturar_post(monkeypatch, fake_response, {"shortUrl": "https://tidd.ly/x"})
        observable.converter_link_kabum("https://www.kabum.com.br/produto/1")
        assert chamadas and chamadas[0].get("timeout")

    def test_aliexpress_tem_timeout(self, monkeypatch, fake_response):
        chamadas = self._capturar_post(monkeypatch, fake_response)
        observable.converter_link_aliexpress("https://pt.aliexpress.com/item/1.html")
        assert chamadas and chamadas[0].get("timeout")

    def test_envio_de_texto_tem_timeout(self, monkeypatch, fake_response):
        chamadas = self._capturar_post(monkeypatch, fake_response)
        observable.enviar_para_meu_bot("oi")
        assert chamadas and chamadas[0].get("timeout")

    def test_envio_de_foto_tem_timeout(self, monkeypatch, fake_response, tmp_path):
        foto = tmp_path / "f.jpg"
        foto.write_bytes(b"x")
        chamadas = self._capturar_post(monkeypatch, fake_response)
        observable.enviar_para_meu_bot_com_imagem("oi", str(foto))
        assert chamadas and chamadas[0].get("timeout")

    def test_aviso_ao_admin_tem_timeout(self, monkeypatch, fake_response):
        chamadas = self._capturar_post(monkeypatch, fake_response)
        assert observable.avisar_admin("oi") is True
        assert chamadas and chamadas[0].get("timeout")

    def test_timeout_de_leitura_na_foto_nao_reenvia_como_texto(self, monkeypatch, tmp_path):
        # O Telegram pode ter publicado a foto sem responder a tempo: reenviar
        # como texto duplicaria o post.
        foto = tmp_path / "f.jpg"
        foto.write_bytes(b"x")
        chamadas = []

        def fake_post(url, **kwargs):
            chamadas.append(url)
            raise requests.ReadTimeout("lento")
        monkeypatch.setattr(observable.requests, "post", fake_post)

        observable.enviar_para_meu_bot_com_imagem("oi", str(foto))
        assert len(chamadas) == 1 and chamadas[0].endswith("/sendPhoto")

    def test_erro_de_conexao_na_foto_cai_para_texto(self, monkeypatch, tmp_path):
        foto = tmp_path / "f.jpg"
        foto.write_bytes(b"x")
        chamadas = []

        def fake_post(url, **kwargs):
            chamadas.append(url)
            raise requests.ConnectionError("caiu")
        monkeypatch.setattr(observable.requests, "post", fake_post)

        observable.enviar_para_meu_bot_com_imagem("oi", str(foto))
        assert [u.rsplit("/", 1)[1] for u in chamadas] == ["sendPhoto", "sendMessage"]


# ----------------------------------------------------------------------
# Termômetro de preço: reação + comentário no post (ou prévia no admin)
# ----------------------------------------------------------------------
URL_KABUM = "https://www.kabum.com.br/produto/1048336/processador-amd-ryzen-5-7600x3d-4-7ghz"
TEXTO_KABUM = "Processador AMD Ryzen 5 7600X3D, 4.7GHz, AM5\n💰POR: R$ 1499\nLINK: https://tidd.ly/abc"


def _semear_mes_passado(precos, chave="kabum:1048336"):
    """Promoções do produto no mês passado (em relação a hoje)."""
    _, mes = historico_precos.dia_e_mes_brt()
    ano, m = map(int, historico_precos.mes_anterior(mes).split("-"))
    for i, preco in enumerate(precos):
        historico_precos.registrar_e_avaliar(chave, preco * 100, datetime(ano, m, 5 + i, 15, tzinfo=timezone.utc))


class _BotApiFalsa:
    """requests.post falso que responde como a Bot API e guarda as chamadas."""

    def __init__(self, monkeypatch, fake_response, falhar=(), resultados=None):
        self.chamadas = []
        self.fake_response = fake_response
        self.falhar = set(falhar)
        self.resultados = resultados or {}
        monkeypatch.setattr(observable.requests, "post", self.post)

    def post(self, url, json=None, **kw):
        metodo = url.rsplit("/", 1)[1]
        self.chamadas.append((metodo, json))
        if metodo in self.falhar:
            return self.fake_response(status_code=400, text='{"description":"REACTION_INVALID"}')
        resultado = self.resultados.get(metodo, {"message_id": 900})
        return self.fake_response(status_code=200, json_data={"ok": True, "result": resultado})

    def metodos(self):
        return [m for m, _ in self.chamadas]


class TestComentarioDePreco:
    def _processar(self, monkeypatch, texto=TEXTO_KABUM, message_id=555):
        def converter(t):
            observable._anotar_url_produto(URL_KABUM)     # como o converter_link_kabum faz
            return t, True
        monkeypatch.setattr(observable, "substituir_links_no_texto", converter)
        publicados = []

        def publicar(*a):
            publicados.append(a)
            return message_id
        monkeypatch.setattr(observable, "publicar", publicar)
        asyncio.run(observable.processar_promocao(_evento(texto)))
        return publicados

    def _telethon_falso(self, monkeypatch, send_message=None):
        canal = object()

        async def get_input_entity(alvo):
            return canal
        enviados = []

        async def enviar(entidade, texto, **kw):
            enviados.append((entidade, texto, kw))
        monkeypatch.setattr(observable.client, "get_input_entity", get_input_entity)
        monkeypatch.setattr(observable.client, "send_message", send_message or enviar)
        monkeypatch.setattr(observable, "ESPERAS_COMENTARIO", (0, 0, 0))
        return canal, enviados

    # --- id do post publicado -------------------------------------------
    def test_publicar_devolve_o_id_do_post(self, monkeypatch, fake_response, tmp_path):
        monkeypatch.setattr(observable.requests, "post", lambda *a, **k: fake_response(
            status_code=200, json_data={"ok": True, "result": {"message_id": 321}}))
        assert observable.publicar("oi") == 321
        foto = tmp_path / "f.jpg"
        Image.new("RGB", (50, 50), "white").save(foto)
        monkeypatch.setattr(observable, "aplicar_marca_dagua", lambda caminho: caminho)
        assert observable.publicar("oi", str(foto)) == 321

    def test_foto_recusada_cai_para_texto_e_devolve_o_id_do_texto(self, monkeypatch, fake_response, tmp_path):
        foto = tmp_path / "f.jpg"
        foto.write_bytes(b"x")
        respostas = iter([fake_response(status_code=400, text="caption too long"),
                          fake_response(status_code=200, json_data={"result": {"message_id": 654}})])
        monkeypatch.setattr(observable.requests, "post", lambda *a, **k: next(respostas))
        assert observable.enviar_para_meu_bot_com_imagem("oi", str(foto)) == 654

    def test_timeout_da_foto_fica_sem_id(self, monkeypatch, tmp_path):
        foto = tmp_path / "f.jpg"
        foto.write_bytes(b"x")

        def lento(*a, **k):
            raise requests.ReadTimeout("lento")
        monkeypatch.setattr(observable.requests, "post", lento)
        assert observable.enviar_para_meu_bot_com_imagem("oi", str(foto)) is None

    # --- modos ------------------------------------------------------------
    def test_modo_teste_manda_previa_ao_admin(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "teste")
        _semear_mes_passado([1799, 1749, 1799, 1799])        # R$ 1.499 = 16% abaixo
        api = _BotApiFalsa(monkeypatch, fake_response)

        async def proibido(*a, **k):
            raise AssertionError("o modo teste não usa a conta do Telethon")
        monkeypatch.setattr(observable.client, "send_message", proibido)

        publicados = self._processar(monkeypatch)

        assert len(publicados) == 1                          # o post sai normal no canal
        # Chat do admin sem grupo de discussão: o comentário vai como resposta à cópia.
        assert api.metodos() == ["copyMessage", "setMessageReaction", "getChat", "sendMessage"]
        copia, reacao, _, comentario = (payload for _, payload in api.chamadas)
        assert copia == {"chat_id": "111", "from_chat_id": observable.MEU_CANAL_ID, "message_id": 555}
        assert reacao["message_id"] == 900 and reacao["reaction"] == [{"type": "emoji", "emoji": "🔥"}]
        assert comentario["reply_parameters"] == {"message_id": 900}
        assert comentario["text"].startswith("🔥 <b>Preço excelente!</b>")
        assert historico_precos.OBSERVACAO in comentario["text"]

    def test_modo_teste_comenta_na_discussao_do_admin(self, monkeypatch, fake_response):
        # ADMIN LOG com grupo de discussão (como o canal principal): o
        # comentário vai na thread da cópia, assinado pelo próprio ADMIN LOG,
        # pelo mesmo caminho que o canal principal usa no modo "ligado".
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "teste")
        _semear_mes_passado([1599, 1599])
        api = _BotApiFalsa(monkeypatch, fake_response,
                           resultados={"getChat": {"id": 111, "linked_chat_id": -100777}})
        canal, enviados = self._telethon_falso(monkeypatch)

        self._processar(monkeypatch)

        assert api.metodos() == ["copyMessage", "setMessageReaction", "getChat"]
        entidade, texto, kw = enviados[0]
        assert entidade is canal and kw["send_as"] is canal
        assert kw["comment_to"] == 900                       # a cópia no ADMIN LOG, não o post do canal
        assert texto.startswith("✅ <b>Bom momento pra comprar!</b>")

    def test_modo_ligado_reage_e_comenta_como_o_canal(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _semear_mes_passado([1599, 1599])                    # R$ 1.499 = 6% abaixo
        api = _BotApiFalsa(monkeypatch, fake_response)
        canal, enviados = self._telethon_falso(monkeypatch)

        self._processar(monkeypatch)

        assert api.metodos() == ["setMessageReaction"]
        assert api.chamadas[0][1]["chat_id"] == observable.MEU_CANAL_ID
        assert api.chamadas[0][1]["message_id"] == 555
        assert api.chamadas[0][1]["reaction"] == [{"type": "emoji", "emoji": "👍"}]
        entidade, texto, kw = enviados[0]
        assert entidade is canal and kw["send_as"] is canal and kw["comment_to"] == 555
        assert kw["silent"] is True and kw["parse_mode"] == "html" and kw["link_preview"] is False
        assert texto.startswith("✅ <b>Bom momento pra comprar!</b>")
        assert "📉 6% abaixo da média de" in texto

    def test_anuncio_novo_de_placa_comenta_pela_media_do_modelo(self, monkeypatch, fake_response):
        # Caso real (15/10): MSI RTX 5060 num /up/ do ML sem histórico próprio,
        # com outras RTX 5060 no histórico (média R$ 2.632).
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _, mes = historico_precos.dia_e_mes_brt()
        ano, m = map(int, mes.split("-"))
        for hora, (chave, reais) in enumerate([("amz:B0GQ17JZFJ", 2609), ("kabum:1", 2645), ("shopee:1.2", 2559),
                                               ("meli:p:MLB52175612", 2617), ("meli:p:MLB50869989", 2663),
                                               ("kabum:2", 2699)]):
            historico_precos.registrar_e_avaliar(chave, reais * 100, datetime(ano, m, 1, 12 + hora, tzinfo=timezone.utc),
                                                 modelo="RTX 5060")
        url = ("https://www.mercadolivre.com.br/placa-de-video-msi-rtx-5060-8g-shadow-2x-oc-gddr7-128-bits"
               "/up/MLBU4899388815")

        def converter(t):
            observable._anotar_url_produto(url)          # como o converter_link_meli faz
            return t, True
        monkeypatch.setattr(observable, "substituir_links_no_texto", converter)
        monkeypatch.setattr(observable, "publicar", lambda *a: 555)
        api = _BotApiFalsa(monkeypatch, fake_response)
        _, enviados = self._telethon_falso(monkeypatch)
        texto = ("Placa De Vídeo Msi Rtx 5060 8g Shadow 2x Oc Gddr7 128 Bits\n\nPOR: R$2.439,99\n"
                 "Cupom: RESGATEALLSITE + Cupom da loja\n\nhttps://meli.la/17jqwgq\n\n(anúncio)")

        asyncio.run(observable.processar_promocao(_evento(texto)))

        assert api.chamadas[0][1]["reaction"] == [{"type": "emoji", "emoji": "👍"}]
        comentario = enviados[0][1]
        assert comentario.startswith("✅ <b>Bom momento pra comprar!</b>")
        assert "📉 7% abaixo da média das RTX 5060 de" in comentario
        assert historico_precos.OBSERVACAO_MODELO.format(modelo="RTX 5060") in comentario

    def test_reacao_recusada_nao_impede_o_comentario(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _semear_mes_passado([1599, 1599])
        api = _BotApiFalsa(monkeypatch, fake_response, falhar={"setMessageReaction"})
        _, enviados = self._telethon_falso(monkeypatch)

        self._processar(monkeypatch)

        assert api.metodos() == ["setMessageReaction", "sendMessage"]   # reação + aviso ao admin
        assert "reação está liberada" in api.chamadas[1][1]["text"]
        assert len(enviados) == 1

    def test_modo_sombra_so_loga(self, monkeypatch, fake_response, capsys):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "sombra")
        _semear_mes_passado([1599, 1599])
        api = _BotApiFalsa(monkeypatch, fake_response)
        self._processar(monkeypatch)
        assert api.chamadas == []
        assert "(sombra) comentaria no post 555" in capsys.readouterr().out

    def test_modo_desligado_nem_registra(self, monkeypatch):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "desligado")
        self._processar(monkeypatch)
        assert historico_precos.registrar_e_avaliar("kabum:1048336", 149900).referencias == ()

    def test_sem_id_do_post_nao_comenta(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "teste")
        _semear_mes_passado([1599, 1599])
        api = _BotApiFalsa(monkeypatch, fake_response)
        self._processar(monkeypatch, message_id=None)
        assert api.chamadas == []

    def test_preco_na_media_nao_comenta(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "teste")
        _semear_mes_passado([1499, 1499])
        api = _BotApiFalsa(monkeypatch, fake_response)
        self._processar(monkeypatch)
        assert api.chamadas == []

    def test_conversao_que_falhou_ainda_registra_o_preco(self, monkeypatch):
        def converter(t):
            observable._anotar_url_produto(URL_KABUM)
            return t, False                                  # ex.: cookie do ML vencido
        monkeypatch.setattr(observable, "substituir_links_no_texto", converter)
        monkeypatch.setattr(observable, "publicar", lambda *a: pytest.fail("não deveria publicar"))
        asyncio.run(observable.processar_promocao(_evento(TEXTO_KABUM)))
        _, mes = historico_precos.dia_e_mes_brt()
        with sqlite3.connect(historico_precos.CAMINHO_BANCO) as con:
            assert con.execute("SELECT n FROM precos_mes WHERE chave = 'kabum:1048336' AND mes = ?",
                               (mes,)).fetchone() == (1,)

    def test_avaliacao_quebrada_nao_derruba_o_post(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("banco corrompido")
        monkeypatch.setattr(historico_precos, "avaliar_oferta", boom)
        assert len(self._processar(monkeypatch)) == 1

    # --- proteções da conta do Telethon --------------------------------------
    def test_post_ainda_nao_chegou_ao_grupo_tenta_de_novo(self, monkeypatch, fake_response, capsys):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _semear_mes_passado([1599, 1599])
        _BotApiFalsa(monkeypatch, fake_response)
        tentativas = []

        async def send_message(entidade, texto, **kw):
            tentativas.append(kw["comment_to"])
            if len(tentativas) < 3:
                raise errors.MsgIdInvalidError(None)
        self._telethon_falso(monkeypatch, send_message)

        self._processar(monkeypatch)

        assert tentativas == [555, 555, 555]
        assert "comentou no post 555" in capsys.readouterr().out

    def test_erro_de_permissao_suspende_e_avisa_uma_vez(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _semear_mes_passado([1599, 1599])
        _BotApiFalsa(monkeypatch, fake_response)
        avisos = []
        monkeypatch.setattr(observable, "avisar_admin_com_cooldown", lambda tipo, texto: avisos.append(tipo))

        async def send_message(*a, **k):
            raise errors.SendAsPeerInvalidError(None)
        self._telethon_falso(monkeypatch, send_message)

        self._processar(monkeypatch)

        assert avisos == ["comentario_preco"]
        assert observable._pode_comentar() is False          # suspenso: o próximo nem tenta

    def test_flood_wait_longo_descarta_sem_esperar(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _semear_mes_passado([1599, 1599])
        _BotApiFalsa(monkeypatch, fake_response)
        tentativas = []

        async def send_message(*a, **k):
            tentativas.append(1)
            raise errors.FloodWaitError(None, capture=3000)
        self._telethon_falso(monkeypatch, send_message)

        inicio = time.monotonic()
        self._processar(monkeypatch)
        assert tentativas == [1]
        assert time.monotonic() - inicio < 5

    def test_envio_travado_e_limitado_pelo_prazo(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _semear_mes_passado([1599, 1599])
        _BotApiFalsa(monkeypatch, fake_response)

        async def travado(*a, **k):
            await asyncio.sleep(3600)
        self._telethon_falso(monkeypatch, travado)
        real_wait_for = asyncio.wait_for

        async def wait_for_rapido(aw, timeout):
            return await real_wait_for(aw, min(timeout, 0.05))
        monkeypatch.setattr(observable.asyncio, "wait_for", wait_for_rapido)

        assert len(self._processar(monkeypatch)) == 1        # o post saiu e o handler voltou

    def test_limite_de_comentarios_por_hora(self, monkeypatch, fake_response):
        monkeypatch.setattr(observable, "MODO_COMENTARIO_PRECO", "ligado")
        _semear_mes_passado([1599, 1599])
        api = _BotApiFalsa(monkeypatch, fake_response)
        _, enviados = self._telethon_falso(monkeypatch)
        agora = time.monotonic()
        observable._comentarios_recentes.extend([agora] * observable.MAX_COMENTARIOS_POR_HORA)

        self._processar(monkeypatch)

        assert api.chamadas == [] and enviados == []

    def test_canal_fora_do_cache_e_achado_nos_dialogos(self, monkeypatch):
        alvo = object()

        async def get_input_entity(x):
            raise ValueError("não está no cache da sessão")

        async def iter_dialogs():
            for dialogo in (SimpleNamespace(id=-1, input_entity=None),
                            SimpleNamespace(id=observable.MEU_CANAL_ID, input_entity=alvo)):
                yield dialogo
        monkeypatch.setattr(observable.client, "get_input_entity", get_input_entity)
        monkeypatch.setattr(observable.client, "iter_dialogs", iter_dialogs)
        assert asyncio.run(observable._entidade(observable.MEU_CANAL_ID)) is alvo
