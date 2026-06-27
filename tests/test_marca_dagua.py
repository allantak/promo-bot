"""
Testes da marca d'água aplicada às imagens das postagens.

Verifica que aplicar_marca_dagua() sobrepõe o selo no canto inferior
direito, preserva as dimensões da foto e é à prova de falha (devolve a
imagem original se algo der errado). Não faz rede.
"""
import os
import tempfile

from PIL import Image

import observable


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
