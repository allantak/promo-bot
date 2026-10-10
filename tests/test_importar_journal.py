"""
Testes do importador do journal (importar_journal.py), com linhas reais do
journal da VPS em tests/fixtures/journal_amostra.txt (set/out 2026, host
trocado por "vps"). Sem rede: o envio ao admin usa requests.post falso.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

import historico_precos as hp
import importar_journal as ij

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "journal_amostra.txt")


def _ler_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return list(ij.ler_posts(f))


def _linha(momento, msg, pid=4242):
    return f"{momento.isoformat()} vps python3[{pid}]: {msg}"


def _post_kabum(momento, preco, pid=4242):
    """Bloco de log de um post da KaBuM como o bot escreve no journal."""
    return [
        _linha(momento, "[filtro] ✅ Dentro do nicho", pid),
        _linha(momento, "[✓] URL limpa para Awin: https://www.kabum.com.br/produto/1048336/"
                        "processador-amd-ryzen-5-7600x3d-4-7ghz", pid),
        _linha(momento, "[!] Oferta de: OQMDV-PROMO", pid),
        _linha(momento, "Texto final:", pid),
        _linha(momento, "Processador AMD Ryzen 5 7600X3D, 4.7GHz, AM5", pid),
        _linha(momento, f"💰POR: R$ {preco}", pid),
        _linha(momento, "LINK: https://tidd.ly/4ydoEQV", pid),
        _linha(momento, "ANUNCIO", pid),
        _linha(momento, "[✓] Postado com imagem no canal!", pid),
    ]


# ----------------------------------------------------------------------
# Leitura dos blocos do journal
# ----------------------------------------------------------------------
class TestLerPosts:
    def test_amostra_real(self):
        posts = _ler_fixture()
        chaves = [hp.chave_unica(p.urls, p.texto)[0] for p in posts]
        precos = [hp.analisar_preco(p.texto)[0].centavos for p in posts]
        assert chaves == [None, "meli:p:MLB46083470", None, "amz:B09BB3DM7M",
                          "meli:p:MLB53471779", "meli:p:MLB52170796", "kabum:1048336", "meli:p:MLB26915018"]
        assert precos == [266700, 12900, 183900, 366100, 264000, 116600, 160100, 179900]

    def test_conversao_que_falhou_nao_vaza_url_para_o_proximo_post(self):
        # Na amostra, o cupom do ML (vitrine com uma manivela de vidro) e a
        # categoria de livros falham antes do post do notebook.
        notebook = next(p for p in _ler_fixture() if "Positivo Vision" in p.texto)
        assert not any("MLB-4001346826" in u or "/c/livros" in u for u in notebook.urls)

    def test_bloco_termina_no_log_seguinte_e_corta_o_rodape(self):
        notebook = next(p for p in _ler_fixture() if "Positivo Vision" in p.texto)
        assert notebook.texto.endswith("tag=brocolis-20")      # rodapé do Poison faz parte do texto...
        assert hp.analisar_preco(notebook.texto)[0].centavos == 264000   # ...mas não do preço

    def test_momento_e_o_da_primeira_linha_do_post(self):
        # Até 02/10 o stdout ia em blocos: o "Texto final" da RTX 3080 chegou ao
        # journal às 12:10, mas o post começou às 03:50.
        rtx = next(p for p in _ler_fixture() if "RTX 3080" in p.texto)
        assert rtx.momento == datetime(2026, 9, 10, 3, 50, 6, tzinfo=timezone.utc)

    def test_troca_de_pid_encerra_o_bloco(self):
        momento = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
        linhas = _post_kabum(momento, 1601)[:6] + [_linha(momento, "Iniciando o observador...", pid=999)]
        posts = list(ij.ler_posts(linhas))
        assert len(posts) == 1
        assert posts[0].texto.endswith("💰POR: R$ 1601")

    def test_linhas_que_nao_sao_do_bot_sao_ignoradas(self):
        linhas = ["2026-10-09T12:00:00+00:00 vps systemd[1]: Started bot-promo.service.", "lixo"]
        assert list(ij.ler_posts(linhas)) == []


# ----------------------------------------------------------------------
# Importação: mesma conta que o bot faz ao vivo
# ----------------------------------------------------------------------
class TestImportar:
    def test_amostra_real(self):
        estatisticas, comentarios = ij.importar(_ler_fixture())
        assert estatisticas["posts"] == 8
        assert estatisticas["motivo:ok"] == 6
        assert estatisticas["motivo:sem_chave"] == 2
        assert comentarios == []          # sem histórico anterior, nada a comentar

    def test_semeia_setembro_e_avalia_outubro(self):
        inicio = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
        linhas = []
        for i, preco in enumerate([1799, 1749, 1799]):
            linhas += _post_kabum(inicio + timedelta(days=i * 7), preco)
        linhas += _post_kabum(datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc), 1601)

        estatisticas, comentarios = ij.importar(ij.ler_posts(linhas))

        assert estatisticas["motivo:ok"] == 4
        assert len(comentarios) == 1
        _, avaliacao, comentario = comentarios[0]
        # Média de setembro R$ 1.782: R$ 1.601 está 10% abaixo. Com só 3
        # promoções o "menor preço" não conta, e 10% < 15%: "bom".
        assert avaliacao.status == "bom"
        assert "📉 10% abaixo da média de setembro (R$ 1.782 em 3 promoções)" in comentario

    def test_rodar_duas_vezes_conta_igual(self, tmp_path):
        caminho = str(tmp_path / "sim.db")
        ij.importar(_ler_fixture(), caminho=caminho)
        ij.importar(_ler_fixture(), caminho=caminho)
        import sqlite3
        with sqlite3.connect(caminho) as con:
            assert con.execute("SELECT SUM(n) FROM precos_mes").fetchone()[0] == 6

    def test_main_com_relatorio(self, capsys, tmp_path):
        with open(FIXTURE, encoding="utf-8") as entrada:
            assert ij.main(["--banco", str(tmp_path / "x.db"), "--relatorio"], entrada=entrada) == 0
        saida = capsys.readouterr().out
        assert "Posts lidos do journal: 8" in saida
        assert "Comentários que teriam saído: 0" in saida


# ----------------------------------------------------------------------
# Exemplos no chat do admin
# ----------------------------------------------------------------------
class TestEnviarAoAdmin:
    def test_envia_post_reacao_e_comentario_em_resposta(self, monkeypatch, fake_response):
        import requests
        chamadas = []

        def fake_post(url, json=None, timeout=None):
            metodo = url.rsplit("/", 1)[1]
            chamadas.append((metodo, json))
            return fake_response(status_code=200, json_data={"result": {"message_id": 77}})
        monkeypatch.setattr(requests, "post", fake_post)

        av = hp.classificar("kabum:1", 160100, "2026-10", {"2026-09": (3, 534700, 174900, 179900)})
        post = ij.PostDoJournal(datetime(2026, 10, 9, 15, tzinfo=timezone.utc), "Ryzen\nPOR: R$ 1601", [])
        enviados = ij.enviar_exemplos_ao_admin([(post, av, hp.texto_comentario(av))], 5, "TOKEN", "111", espera=0)

        assert enviados == 1
        assert [m for m, _ in chamadas] == ["sendMessage", "setMessageReaction", "sendMessage"]
        assert "Exemplo real do histórico (09/10/2026)" in chamadas[0][1]["text"]
        assert chamadas[1][1]["reaction"] == [{"type": "emoji", "emoji": hp.reacao(av)}]
        assert chamadas[2][1]["reply_parameters"] == {"message_id": 77}
        assert hp.OBSERVACAO in chamadas[2][1]["text"]

    def test_le_credenciais_do_env_sem_executar(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_ADMIN_ID", raising=False)
        env = tmp_path / ".env"
        env.write_text('# comentário\nMELI_COOKIE=a=b; c=d $(rm -rf /)\nBOT_TOKEN="123:abc"\n'
                       "export TELEGRAM_ADMIN_ID=111\n", encoding="utf-8")
        assert ij.ler_credenciais(str(env)) == ("123:abc", "111")
