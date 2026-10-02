"""
Testes de robustez: deduplicação, estado persistente, filtro de idade
(catch_up), vigias, handler fora do event loop e timeouts. Sem rede e sem
Telegram (o client é usado só com métodos substituídos por fakes).
"""
import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

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
        texto, caminho_imagem = publicados[0]
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
