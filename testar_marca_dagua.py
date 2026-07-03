import os
import sys
import shutil

# Importa a função real do bot, sem iniciar o Telegram
from observable import aplicar_marca_dagua, CAMINHO_MARCA_DAGUA


def main():
    print("=== Teste de Marca d'Água (Water Maker) ===\n")

    # Passe o caminho da imagem como argumento, ex:
    #   python testar_marca_dagua.py foto.jpg
    # Se não passar, tenta usar uma imagem de teste padrao.
    if len(sys.argv) > 1:
        imagens_teste = sys.argv[1:]
    else:
        imagens_teste = ["teste.jpg"]

    print(f"Marca d'água : {CAMINHO_MARCA_DAGUA}")
    print(f"Existe?      : {os.path.exists(CAMINHO_MARCA_DAGUA)}\n")

    for i, caminho in enumerate(imagens_teste, 1):
        if not os.path.exists(caminho):
            print(f"[X] Imagem não encontrada: {caminho}")
            print("-" * 60)
            continue

        # aplicar_marca_dagua salva num arquivo temporário; copiamos para
        # um nome fixo ao lado para você abrir e conferir o resultado.
        caminho_final = aplicar_marca_dagua(caminho)
        saida = f"resultado_marca_{i}.jpg"
        shutil.copy(caminho_final, saida)

        aplicou = caminho_final != caminho

        print(f"Original   : {caminho}")
        print(f"Resultado  : {os.path.abspath(saida)}")
        print(f"Aplicou?   : {'sim' if aplicou else 'NÃO (fallback - devolveu a original)'}")
        print("-" * 60)


if __name__ == "__main__":
    main()
