"""
Testes da marca d'água aplicada às imagens das postagens.

Verifica que aplicar_marca_dagua() sobrepõe o selo no canto inferior
direito, preserva as dimensões da foto e é à prova de falha (devolve a
imagem original se algo der errado). Não faz rede.
"""
import glob
import os
import tempfile

import pytest
from PIL import Image

import observable


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
# Imagens reais de canais de origem, cada uma com o selo "OQMDV/ANÚNCIO"
# (roxo) no canto inferior direito. Servem para garantir que a nossa marca
# d'água cobre esse selo em fotos de tamanhos variados (720px–1280px).
IMAGENS_COM_SELO = sorted(glob.glob(os.path.join(FIXTURES_DIR, "com_selo_*.jpg")))


def _e_pixel_roxo(r, g, b) -> bool:
    """Heurística que casa com o roxo/magenta saturado do selo do canal de
    origem (R e B altos, G baixo), evitando azuis e cinzas."""
    return r > 90 and b > 90 and g < r * 0.7 and g < b * 0.7 and (r + b) > 2 * g + 60


def _contar_roxo_no_canto(caminho: str) -> int:
    """Conta pixels roxos do selo no canto inferior direito (55%–100% da
    imagem), onde o selo do canal de origem sempre aparece."""
    with Image.open(caminho) as img:
        im = img.convert("RGB")
        W, H = im.size
        px = im.load()
        return sum(
            1
            for y in range(int(H * 0.55), H)
            for x in range(int(W * 0.55), W)
            if _e_pixel_roxo(*px[x, y])
        )


def _criar_foto_base(cor=(255, 0, 0), tamanho=(800, 600)) -> str:
    """Cria uma foto JPEG de cor sólida (como uma imagem baixada) e
    devolve o caminho."""
    caminho = tempfile.mktemp(suffix=".jpg")
    Image.new("RGB", tamanho, cor).save(caminho, "JPEG", quality=95)
    return caminho


def _diff(p1, p2):
    """Soma das diferenças absolutas por canal entre dois pixels RGB."""
    return sum(abs(a - b) for a, b in zip(p1, p2))


class TestAplicarMarcaDagua:
    def test_gera_nova_imagem_preservando_dimensoes(self):
        base = _criar_foto_base()
        try:
            saida = observable.aplicar_marca_dagua(base)
            assert saida != base
            assert os.path.exists(saida)
            try:
                with Image.open(saida) as img:
                    assert img.format == "JPEG"
                    assert img.size == (800, 600)
            finally:
                os.remove(saida)
        finally:
            os.remove(base)

    def test_selo_no_canto_inferior_direito(self):
        base = _criar_foto_base(cor=(255, 0, 0))
        try:
            saida = observable.aplicar_marca_dagua(base)
            try:
                with Image.open(base) as img_in, Image.open(saida) as img_out:
                    entrada = img_in.convert("RGB")
                    resultado = img_out.convert("RGB")

                    # Canto superior esquerdo: longe do selo → praticamente igual
                    assert _diff(entrada.getpixel((10, 10)),
                                 resultado.getpixel((10, 10))) < 30

                    # Centro do selo (canto inferior direito): mudou bastante
                    largura = int(800 * observable.MARCA_DAGUA_FRACAO)
                    margem = int(800 * observable.MARCA_DAGUA_MARGEM_FRACAO)
                    cx = 800 - margem - largura // 2   # centro do selo (canto inf. direito)
                    cy = 600 - margem - largura // 2   # selo é quadrado
                    assert _diff(entrada.getpixel((cx, cy)),
                                 resultado.getpixel((cx, cy))) > 60
            finally:
                os.remove(saida)
        finally:
            os.remove(base)

    def test_fallback_quando_marca_ausente(self, monkeypatch):
        monkeypatch.setattr(
            observable, "CAMINHO_MARCA_DAGUA",
            os.path.join(tempfile.gettempdir(), "nao_existe_marca_dagua.png"),
        )
        base = _criar_foto_base()
        try:
            saida = observable.aplicar_marca_dagua(base)
            # Sem marca disponível, devolve o caminho original sem erro
            assert saida == base
        finally:
            os.remove(base)


class TestCobreSeloDoCanalDeOrigem:
    """Regressão do tamanho da marca (MARCA_DAGUA_FRACAO): a marca precisa
    cobrir por completo o selo do canal de origem em fotos reais de tamanhos
    variados. Se alguém reduzir demais a fração, estes testes quebram."""

    def test_fixtures_existem(self):
        assert IMAGENS_COM_SELO, (
            "Nenhuma fixture com_selo_*.jpg encontrada em tests/fixtures/"
        )

    @pytest.mark.parametrize("caminho", IMAGENS_COM_SELO,
                             ids=lambda p: os.path.basename(p))
    def test_selo_original_e_detectavel(self, caminho):
        # Sanidade: a fixture realmente tem o selo roxo no canto (senão o
        # teste de cobertura passaria por engano).
        assert _contar_roxo_no_canto(caminho) > 20

    @pytest.mark.parametrize("caminho", IMAGENS_COM_SELO,
                             ids=lambda p: os.path.basename(p))
    def test_marca_cobre_o_selo(self, caminho):
        saida = observable.aplicar_marca_dagua(caminho)
        # aplicar_marca_dagua nunca deve cair no fallback (devolver a original)
        # para uma imagem válida; senão o selo não seria coberto.
        assert saida != caminho
        try:
            assert _contar_roxo_no_canto(saida) == 0, (
                f"Selo do canal de origem ainda visível em {os.path.basename(caminho)} "
                f"com MARCA_DAGUA_FRACAO={observable.MARCA_DAGUA_FRACAO}"
            )
        finally:
            os.remove(saida)
