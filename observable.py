from telethon import TelegramClient, events, errors
from cachetools import TTLCache
import asyncio
import html
import json
import re
import requests
import hashlib
import hmac
import signal
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qsl, urlparse, parse_qs, urlencode, urlunparse, unquote, urljoin
from collections import Counter, deque
import tempfile

from PIL import Image
from dotenv import load_dotenv
import os

import historico_precos

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
MEU_CANAL_ID = int(os.getenv('MEU_CANAL_ID'))
ALIEXPRESS_APP_KEY = os.getenv('ALIEXPRESS_APP_KEY')
ALIEXPRESS_APP_SECRET = os.getenv('ALIEXPRESS_APP_SECRET')
SHOPEE_APP_ID = os.getenv('SHOPEE_APP_ID')
SHOPEE_SECRET = os.getenv('SHOPEE_SECRET')
AMAZON_ASSOCIATE_TAG = os.getenv('AMAZON_ASSOCIATE_TAG')
AWIN_PUBLISHER_ID = os.getenv('AWIN_PUBLISHER_ID')
AWIN_ACCESS_TOKEN = os.getenv('AWIN_ACCESS_TOKEN')
AWIN_KABUM_ADVERTISER_ID = int(os.getenv('AWIN_KABUM_ADVERTISER_ID'))
ALIEXPRESS_TRACKING_ID = 'default'

MELI_COOKIE = os.getenv('MELI_COOKIE')
MELI_X_CSRF_TOKEN = os.getenv('MELI_X_CSRF_TOKEN')
MELI_AFFILIATE_TAG = os.getenv('MELI_AFFILIATE_TAG')

ADMIN_CHAT_ID = os.getenv('TELEGRAM_ADMIN_ID')

# Encurtadores genéricos: podem apontar para qualquer loja, então o link precisa
# ser expandido antes de detectar a plataforma (o slug não é confiável).
ENCURTADORES_GENERICOS = ['aoferta.net']

# Alerta de sessão expirada do ML: janela mínima entre dois avisos ao admin (segundos).
ALERTA_EXPIRACAO_COOLDOWN = 1800
_ultimo_alerta_expiracao = 0.0

# --- Marca d'água nas imagens das postagens ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAMINHO_MARCA_DAGUA = os.path.join(BASE_DIR, 'waterMaker.png')
MARCA_DAGUA_FRACAO = 0.19          # largura do selo ~19% da foto (menor valor que ainda cobre o selo do canal de origem no canto, testado em imagens 720px–1280px)
MARCA_DAGUA_MARGEM_FRACAO = 0.0    # selo encostado no canto inferior direito

# --- Robustez em produção (VPS de 1 GB, conexão com o Telegram instável) ---
# Com catch_up=True o Telethon entrega, ao reiniciar, o que chegou enquanto o bot
# estava fora; ofertas mais velhas que isto são descartadas (preço/cupom vencido).
# Folga: após um restart o Telethon pode levar até ~15 min para buscar um canal.
IDADE_MAXIMA_POST = timedelta(minutes=60)
# Vigia de updates: a cada VIGIA_INTERVALO compara o último post de cada canal
# com o último que o handler viu. Se um post ficar VIGIA_TOLERANCIA sem chegar,
# em VIGIA_FALHAS_MAX checagens seguidas, o bot está "surdo" e é reiniciado.
# O próprio Telethon refaz a busca de cada canal a cada 15 min; a tolerância faz o
# reinício cair sempre depois disso (2ª checagem ruim entre ~16 e 19 min).
VIGIA_INTERVALO = 180
VIGIA_TOLERANCIA = 780
VIGIA_FALHAS_MAX = 2
# Prazo de um post inteiro (conversão + imagem + envio) segurando a fila.
PRAZO_POR_POST = 300
# Vigia do event loop: uma thread separada derruba o processo se o loop ficar
# BATIMENTO_LIMITE segundos sem bater (algo bloqueou o asyncio).
BATIMENTO_INTERVALO = 30
BATIMENTO_LIMITE = 600
# Avisos ao admin em caminhos que podem se repetir (restart em laço): no máximo
# um por tipo nesta janela. O controle fica em disco porque cada restart é um
# processo novo.
AVISO_COOLDOWN = 3600
# Estado que precisa sobreviver a restarts (último post tratado por canal e
# horário dos últimos avisos). Fica fora do git (.gitignore).
CAMINHO_ESTADO = os.path.join(BASE_DIR, 'estado_bot.json')
# Código de saída para sessão do Telegram inválida: o systemd não deve reiniciar
# (RestartPreventExitStatus=78), porque só um login manual resolve.
SAIDA_SESSAO_INVALIDA = 78
# Imagem maior que isso (enviada como arquivo) não é baixada: o Pillow
# precisaria de centenas de MB para aplicar a marca d'água numa VPS de 1 GB.
TAMANHO_MAXIMO_IMAGEM = 10 * 1024 * 1024
LADO_MAXIMO_IMAGEM = 2560

# --- Termômetro de preço (historico_precos.py): reação + comentário no post ---
# quando a oferta está abaixo da média das promoções do produto.
#   desligado: nem registra o histórico
#   sombra:    registra e só loga o que comentaria
#   teste:     o canal segue igual; a cópia do post + reação + comentário vão
#              para o chat do admin, para ver como fica antes de ligar
#   ligado:    reage e comenta no post do canal, assinando como o canal
MODO_COMENTARIO_PRECO = (os.getenv('COMENTARIO_PRECO') or 'teste').strip().lower()
# O post leva alguns segundos para chegar ao grupo de discussão do canal.
ESPERAS_COMENTARIO = (2, 4, 8)
PRAZO_COMENTARIO = 60
# A conta que comenta é a mesma que escuta os canais: nada de rajada.
MAX_COMENTARIOS_POR_HORA = 30
SUSPENSAO_COMENTARIOS = 6 * 3600
SUSPENSAO_PEER_FLOOD = 24 * 3600
# Links curtos sem ID do produto: resolvidos só pelo cabeçalho do redirect.
ENCURTADORES_SEM_ID = ['s.shopee.com.br', 'shope.ee', 's.click.aliexpress.com', 'a.aliexpress.com']
MAX_RESOLUCOES_POR_POST = 3


CANAIS_ALVO = [
    '@PoisonPromos',
    '@OQMDVPROMO'
]

# Cache com TTL de 5 minutos e máximo de 500 entradas
# A chave será o hash do título, o valor é irrelevante (usamos True)
cache_links = TTLCache(maxsize=500, ttl=300)

# Caminho absoluto: a sessão não pode depender do diretório de onde o bot é iniciado.
# catch_up=True: ao (re)iniciar, busca o que foi postado enquanto o bot estava fora.
client = TelegramClient(os.path.join(BASE_DIR, 'minha_sessao'), API_ID, API_HASH, catch_up=True)
padrao_link = re.compile(r'https?://\S+')

# URLs de produto que os conversores resolvem durante a conversão de UM post
# (vitrine do ML, redirect da Amazon, Awin da KaBuM...): o histórico de preços
# identifica o produto por elas sem repetir nenhuma requisição. É por thread
# porque cada post é convertido numa thread do asyncio.to_thread.
_coleta = threading.local()


def _anotar_url_produto(url: str):
    urls = getattr(_coleta, 'urls', None)
    if urls is not None and url:
        urls.append(url)


# Destino dos links curtos de Shopee/AliExpress (o link de cupom se repete em
# dezenas de posts: resolve uma vez só).
cache_destinos = TTLCache(maxsize=1000, ttl=6 * 3600)
_trava_destinos = threading.Lock()

# ============================================================
# RODAPÉ / ASSINATURA DE CANAL — linhas que devem ser removidas
# ============================================================
PADROES_RODAPE = [
    re.compile(r'🐈'),                       # marcador específico desse canal
    re.compile(r't\.me/\S+', re.IGNORECASE),  # menção solta a outro canal (sem ser link de produto)
    re.compile(r'receba notifica', re.IGNORECASE),
    re.compile(r'@\w*ALERTABOT', re.IGNORECASE),
]


def remover_rodape(texto: str) -> str:
    linhas = texto.split('\n')
    linhas_limpas = [
        linha for linha in linhas
        if not any(padrao.search(linha) for padrao in PADROES_RODAPE)
    ]

    texto_limpo = '\n'.join(linhas_limpas)
    texto_limpo = re.sub(r'\n{3,}', '\n\n', texto_limpo)
    return texto_limpo.strip()


# Parâmetros de rastreio de terceiros que devem ser removidos
PARAMS_AFILIADO_TERCEIRO = {
    'aw_affid', 'awc', 'sv1', 'sv_campaign_id',
    'utm_source', 'utm_medium', 'utm_campaign',
    'utm_content', 'utm_term'
}

PALAVRAS_FORA_NICHO = {
    # Moda e vestuário
    'camisa', 'camiseta', 'blusa', 'vestido', 'calça', 'calca', 'condicionado', 'ar-condicionado', 'electrolux', 'tv',
    'shorts', 'cueca', 'calcinha', 'sutiã', 'sutia', 'meia',
    'tênis', 'tenis', 'sapato', 'bota', 'sandália', 'sandalia',
    'chinelo', 'tamanco', 'salto', 'mocassim',
    'bolsa', 'carteira', 'pochete', 'mochila de tecido',
    'óculos de sol', 'oculos de sol', 'relógio', 'relogio',
    'pulseira', 'colar', 'brinco', 'anel', 'cordão',
    'perfume', 'colônia', 'roupa', 'jaqueta', 'casaco', 'moletom',
    'pijama', 'bermuda', 'chapéu', 'chapeu', 'boné', 'bone',
    'gravata', 'cinto', 'lenço', 'smartwatch',
    'calçado', 'calcado', 'sapatênis', 'sapatenis',

    # Casa, cozinha e utilidades domésticas
    'panela', 'frigideira', 'wok', 'forma', 'assadeira',
    'liquidificador', 'batedeira', 'mixer', 'espremedor',
    'cafeteira', 'air fryer', 'fritadeira', 'microondas',
    'geladeira', 'refrigerador', 'freezer', 'fogão', 'fogao',
    'forno', 'churrasqueira', 'grill',
    'máquina de lavar', 'lava louça', 'lavadora', 'secadora',
    'aspirador de pó', 'vassoura', 'rodo', 'esfregão',
    'tapete', 'cortina', 'persiana', 'lustre', 'abajur',
    'travesseiro', 'cobertor', 'edredom', 'lençol', 'toalha',
    'colchão', 'cama', 'sofá', 'sofa', 'poltrona', 'mesa de jantar',
    'estante', 'guarda roupa', 'armário',
    'porta retrato', 'vaso', 'quadro', 'espelho',
    'garrafa térmica', 'copo', 'prato', 'tigela', 'talheres',
    'faca', 'jogo de faca', 'jogo americano', 'xícara', 'xicara',
    'organizador', 'organizadora', 'cabide', 'cabideiro', 'porta sabão', 'iphone', 'ipad', 'MacBook', 'Apple Watch', 'AirPods', 'impressora', 'samsung galaxy', 'power bank', 'carregador portátil', 'ar condicionado', 'ketchup', 'mostarda', 'maionese', 'refrigerante', 'suco', 'água mineral', 'agua mineral', 'cerveja artesanal', 'vinho tinto', 'whisky escocês', 'suplemento alimentar', 'barra de proteína', 'ração para cachorro', 'ração para gato', 'coleira para cachorro', 'cama de cachorro', 'arranhador para gato', 'aquário para peixes', 'gaiola para pássaros', 'bicicleta de estrada', 'bike de montanha', 'esteira ergométrica', 'elíptico doméstico', 'halteres ajustáveis', 'anilha de peso olímpica', 'kettlebell de ferro fundido', 'barra de musculação olímpica', 'tapete de yoga antiderrapante', 'aula de yoga online', 'natação em piscina coberta', 'chuteira de futebol society', 'bola de futebol oficial da FIFA',
    'caixa de som',
    # Ferramentas e construção
    'furadeira', 'parafusadeira', 'martelete', 'esmerilhadeira', 'cooktop',
    'serra', 'serrote', 'martelo', 'chave de fenda', 'alicate',
    'trena', 'nível', 'fita isolante', 'cimento', 'argamassa',
    'tinta', 'pincel', 'rolo de pintura', 'lixa', 'lixadeira', 'mangueira',
    # (singular: o filtro já aceita o plural, "jogo de chaves" casa 'jogo de chave')
    'jogo de chave', 'jogo de ferramenta', 'jogo de broca', 'jogo de soquete',

    # Beleza, higiene e saúde
    'shampoo', 'condicionador', 'creme de cabelo', 'máscara capilar',
    'hidratante', 'protetor solar', 'creme facial',
    'maquiagem', 'base líquida', 'base liquida', 'base facial', 'base de maquiagem', 'batom', 'esmalte', 'blush', 'sombra',
    'depilador', 'depiladora', 'barbeador', 'aparelho de barbear', 'lâmina',
    'secador de cabelo', 'chapinha', 'modelador', 'modeladora', 'prancha',
    'escova de dente', 'fio dental', 'enxaguante',
    'absorvente', 'fraldas adulto',
    'suplemento', 'whey', 'creatina', 'proteína', 'proteina', 'copa', 'fifa', 'powerbank',
    'vitamina', 'remédio', 'remedio', 'medicamento',
    'termômetro', 'termometro', 'oxímetro', 'oximetro',
    'aparelho de pressão', 'balança',

    # Bebês e crianças (não tech)
    'fralda', 'mamadeira', 'chupeta', 'berço', 'berce',
    'carrinho de bebê', 'banheira de bebê', 'pomada',
    'boneca', 'boneco', 'massinha', 'lego', 'quebra cabeça',
    'brinquedo', 'pelúcia', 'pelucia',

    # Alimentos e bebidas
    'café', 'cafe', 'biscoito', 'bolacha', 'chocolate',
    'açúcar', 'acucar', 'farinha', 'azeite', 'óleo', 'oleo',
    'arroz', 'feijão', 'feijao', 'macarrão', 'macarrao',
    'leite', 'queijo', 'iogurte', 'manteiga',
    'cerveja', 'vinho', 'whisky', 'energético', 'energetico',
    'suplemento alimentar', 'barra de cereal',

    # Pets
    'ração', 'racao', 'ração para cão', 'ração para gato',
    'coleira', 'guia para cachorro', 'guia retrátil', 'cama de cachorro', 'arranhador',
    'aquario para peixes', 'gaiola',

    # Esporte (não gamer)
    'bicicleta', 'bike', 'esteira', 'elíptico', 'eliptico',
    'halteres', 'halter', 'anilha', 'kettlebell', 'barra',
    'tapete de yoga', 'natação', 'natacao',
    'chuteira', 'bola de futebol', 'luva de boxe',
    'raquete', 'skate', 'patins', 'capacete de bike',

    # Automotivo (não tech)
    'pneu', 'óleo de motor', 'oleo de motor', 'cera automotiva',
    'banco de carro', 'tapete de carro', 'limpador de para brisa',
    'cheiro de carro',

    # Livros e papelaria (não tech)
    'romance', 'bíblia', 'biblia', 'autoajuda', 'auto ajuda',
    'livro de receitas', 'caderno', 'caneta', 'lápis', 'lapis',
    'mochila escolar',

    # Jardinagem
    'vaso de planta', 'terra para planta', 'adubo', 'fertilizante',
    'regador', 'pá de jardim', 'tesoura de poda', 'smartphone', 'celular', 'mochila'
}


def extrair_buy_box_winner(url: str) -> str:
    url_corrigida = url.replace('&amp;', '&')
    parsed = urlparse(url_corrigida)
    # wid pode estar na query OU no fragmento (#...&wid=MLB...)
    query_params = dict(parse_qsl(parsed.query) + parse_qsl(parsed.fragment))

    if 'wid' in query_params:
        match = re.search(r'MLB-?\d+', query_params['wid'])
        if match:
            return match.group(0).replace('-', '')

    if 'pdp_filters' in query_params:
        filtro_descodificado = unquote(query_params['pdp_filters'])
        match = re.search(r'MLB-?\d+', filtro_descodificado)
        if match:
            return match.group(0).replace('-', '')

    match = re.search(r'MLB-?\d+', parsed.path)
    if match:
        return match.group(0).replace('-', '')

    return ""


def limpar_url_produto(url_suja: str) -> str:
    url_suja = url_suja.replace('&amp;', '&')

    parsed = urlparse(url_suja)
    # O ML coloca o wid depois do '#', e o urlparse joga isso em
    # parsed.fragment, não em parsed.query. Lemos os dois.
    query_params = parse_qsl(parsed.query) + parse_qsl(parsed.fragment)

    params_essenciais = []
    for key, value in query_params:
        if key in ['pdp_filters', 'wid']:
            params_essenciais.append((key, value))

    nova_query = urlencode(params_essenciais)

    url_limpa = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if nova_query:
        url_limpa += f"?{nova_query}"

    return url_limpa


def extrair_id_mlb(url: str) -> str:
    match = re.search(r'MLB-?\d+', url.replace('-', ''))
    return match.group(0) if match else ""


def _url_limpa_produto(url: str) -> str:
    """Devolve só esquema + domínio + caminho do produto, sem query/fragmento."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def _mlb_normalizado(url: str) -> str:
    m = re.search(r'MLB-?\d+', url)
    return m.group(0).replace('-', '') if m else ""


def desempacotar_link(url_curta: str) -> str:
    """Resolve um link curto/social do Mercado Livre e devolve a URL LIMPA do
    produto (sem parâmetros). Em páginas de vitrine (/social/...) extrai o
    produto em DESTAQUE. Retorna None se não houver um produto identificável.

    Obs.: não dependemos mais do `wid`. Testes provaram que ele é irrelevante
    para o destino — o que importa é apontar para a URL do produto em si."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        resposta = requests.get(url_curta, headers=headers, allow_redirects=True, timeout=10)
        url_atual = resposta.url

        # Desescapa barras (\/) e aspas (\") do JSON embutido na página.
        html_limpo = resposta.text.replace('\\/', '/').replace('\\"', '"').replace('&amp;', '&')

        # Caso 1: o redirect já caiu direto num produto.
        if extrair_id_mlb(url_atual) and "/social/" not in url_atual:
            return _url_limpa_produto(url_atual)

        # Caso 2: caímos numa vitrine /social/ — extrair o produto em destaque.
        if "/social/" in url_atual:
            padrao = r'https://[a-z.]*mercadolivre\.com\.br/[^\s"\'<>]*MLB-?\d+[^\s"\'<>]*'
            matches = re.findall(padrao, html_limpo)

            if not matches:
                print(f"[X] Nenhum produto encontrado dentro do link social: {url_atual}")
                return None

            # 2a) URL marcada explicitamente como card de destaque da vitrine.
            destaque = next((u for u in matches if 'card-featured' in u), None)

            # 2b) senão, o produto cujo MLB mais se repete na página = destaque.
            if not destaque:
                mais_comum = Counter(_mlb_normalizado(u) for u in matches).most_common(1)[0][0]
                destaque = next((u for u in matches if _mlb_normalizado(u) == mais_comum), matches[0])

            link_produto = _url_limpa_produto(destaque)
            print(f"[🔎] Produto em destaque extraído da vitrine: {link_produto}")
            return link_produto

        return _url_limpa_produto(url_atual)

    except Exception as e:
        print(f"[X] Erro ao desempacotar o link {url_curta}: {e}")
        return None


def enviar_alerta_expiracao(status_code: int):
    """Avisa o admin no Telegram que a sessão do Mercado Livre expirou.

    Disparado no 401/403 da API de afiliados: a partir daí NENHUM link do ML é
    convertido, então o aviso precisa chegar na hora — em produção o print no
    log passa batido. Usa a Bot API via requests (síncrono, igual aos outros
    envios do módulo) para poder ser chamado de dentro do converter_link_meli.
    """
    global _ultimo_alerta_expiracao

    if not ADMIN_CHAT_ID:
        print("[!] TELEGRAM_ADMIN_ID não configurado — alerta de expiração não enviado.")
        return

    # Uma rajada de ofertas do ML gera um 403 por link; alerta só uma vez por janela.
    agora = time.monotonic()
    if _ultimo_alerta_expiracao and agora - _ultimo_alerta_expiracao < ALERTA_EXPIRACAO_COOLDOWN:
        print("[i] Alerta de expiração silenciado (já avisado há pouco).")
        return

    horario = datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M')
    texto = (
        "⚠️ <b>Mercado Livre: sessão expirada</b>\n\n"
        f"A API de afiliados respondeu <b>{status_code}</b> às {horario} (BRT).\n"
        "Nenhum link do Mercado Livre está sendo convertido até isso ser corrigido.\n\n"
        "👉 Atualize <code>MELI_COOKIE</code> e <code>MELI_X_CSRF_TOKEN</code> "
        "no <code>.env</code> e reinicie o bot."
    )

    url_api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
    }

    # O try cobre só a chamada de rede: um print que falhe (console cp1252 no
    # Windows não engole emoji) não pode ser confundido com falha de envio.
    try:
        resposta = requests.post(url_api, json=payload, timeout=10)
    except Exception as e:
        print(f"[X] Erro ao alertar o admin: {e}")
        return

    if resposta.status_code == 200:
        # Só entra em cooldown se o aviso realmente saiu.
        _ultimo_alerta_expiracao = agora
        print("[v] Alerta de expiracao enviado ao admin.")
    else:
        print(f"[X] Falha ao alertar o admin: {resposta.status_code} - {resposta.text[:200]}")


def converter_link_meli(url_original: str) -> str:
    """Gera o SEU short_url de afiliado (meli.la/...) pela API do programa de
    afiliados do Mercado Livre.

    A descoberta-chave: para a API devolver um short_url cujo `ref` ABRE O
    PRODUTO (e não a página /social/.../lists, a "vitrine"), a URL enviada
    precisa carregar o parâmetro `offer_type=BEST_PRICE`. Sem ele, o ML gera um
    `ref` genérico que cai na lista de recomendações do perfil. Não é preciso
    `wid`, fragmento (#...) nem os parâmetros matt_* — só a URL do produto
    (/p/MLBxxxx) + offer_type=BEST_PRICE + a tag do afiliado.
    """
    if not MELI_COOKIE or not MELI_X_CSRF_TOKEN or not MELI_AFFILIATE_TAG:
        print("[!] Credenciais do Mercado Livre incompletas no .env "
              "(MELI_COOKIE / MELI_X_CSRF_TOKEN / MELI_AFFILIATE_TAG).")
        return None

    print(f"[⏳] Processando link recebido: {url_original}")

    url_produto = desempacotar_link(url_original)

    if not url_produto:
        print("[!] Não foi possível chegar a um produto válido. Ignorando.")
        return None
    _anotar_url_produto(url_produto)

    # offer_type=BEST_PRICE é o que faz o short_url apontar para o produto.
    parsed = urlparse(url_produto)
    query = dict(parse_qsl(parsed.query))
    query['offer_type'] = 'BEST_PRICE'
    url_para_api = urlunparse(parsed._replace(query=urlencode(query)))
    print(f"[📤] URL enviada à API: {url_para_api}")

    url_api = "https://www.mercadolivre.com.br/affiliate-program/api/v2/stripe/user/links"
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "cookie": MELI_COOKIE,
        "x-csrf-token": MELI_X_CSRF_TOKEN,
        "origin": "https://www.mercadolivre.com.br",
        "referer": url_para_api.split('#', 1)[0],
        "user-agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36",
    }
    payload = {"url": url_para_api, "tag": MELI_AFFILIATE_TAG}

    try:
        resposta = requests.post(url_api, headers=headers, json=payload, timeout=10)

        if resposta.status_code == 200:
            dados = resposta.json()
            link_afiliado = dados.get("short_url") or dados.get("link") or dados.get("url")
            if link_afiliado:
                print(f"[✓] Link de afiliado gerado: {link_afiliado}")
                return link_afiliado
            print(f"[!] API não retornou short_url: {dados}")
            return None

        if resposta.status_code in (401, 403):
            print(f"[⚠️] {resposta.status_code}: sessão/CSRF do Mercado Livre expirou — "
                  "atualize MELI_COOKIE e MELI_X_CSRF_TOKEN no .env.")
            enviar_alerta_expiracao(resposta.status_code)
            return None

        print(f"[X] Erro API ML: {resposta.status_code} - {resposta.text[:200]}")
        return None

    except Exception as e:
        print(f"[X] Erro no requests ML: {e}")
        return None


# Casa cada termo como palavra inteira (plural opcional), nunca como pedaço de
# outra palavra: 'anel' não pode barrar "janela", 'ração' não pode barrar
# "geração", 'regador' não pode barrar "carregador". Termos mais longos primeiro,
# para o log mostrar o mais específico ('caixa de som' antes de 'caixa').
_PADRAO_FORA_NICHO = re.compile(
    r'(?<!\w)('
    + '|'.join(sorted({re.escape(p.lower()) for p in PALAVRAS_FORA_NICHO}, key=len, reverse=True))
    + r')(?:s|es)?(?!\w)'
)


def e_do_nicho(texto: str) -> bool:
    # URLs ficam de fora: links curtos têm letras aleatórias ("meli.la/2MCZETv" casava 'tv').
    texto_sem_links = re.sub(r'https?://\S+', ' ', texto.lower())

    achado = _PADRAO_FORA_NICHO.search(texto_sem_links)
    if achado:
        print(f"[filtro] ❌ Rejeitado — '{achado.group(1)}'")
        return False

    print(f"[filtro] ✅ Dentro do nicho")
    return True


def limpar_url_kabum(url: str) -> str:
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        params_limpos = {
            k: v for k, v in params.items()
            if k not in PARAMS_AFILIADO_TERCEIRO
        }

        nova_query = urlencode(params_limpos, doseq=True)
        url_limpa = urlunparse(parsed._replace(query=nova_query))

        print(f"[✓] URL limpa: {url_limpa}")
        return url_limpa

    except Exception as e:
        print(f"[X] Erro ao limpar URL: {e}")
        return url


def extrair_url_limpa_kabum(url_encurtada: str) -> str:
    try:
        resposta = requests.get(
            url_encurtada,
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        url_expandida = resposta.url
        print(f"[→] Expandida: {url_expandida}")

        if 'awin1.com' in url_expandida:
            parsed = urlparse(url_expandida)
            params = parse_qs(parsed.query)
            ued = params.get('ued', [None])[0]
            if ued:
                url_destino = unquote(ued)
                print(f"[→] Destino extraído: {url_destino}")
                return limpar_url_kabum(url_destino)

        if 'kabum.com.br' in url_expandida:
            return limpar_url_kabum(url_expandida)

        return None

    except Exception as e:
        print(f"[X] Erro: {e}")
        return None


def converter_link_kabum(url_original: str) -> str:
    dominios_encurtados = ['tidd.ly', 'eioferta.com.br', 'ofertou.xyz', 'awin1.com']
    if any(d in url_original for d in dominios_encurtados):
        print(f"[~] Link encurtado/terceiro detectado, extraindo URL original...")
        url_original = extrair_url_limpa_kabum(url_original)

    if not url_original or 'kabum.com.br' not in url_original:
        print(f"[!] Link ignorado — não é KaBuM: {url_original}")
        return None

    _anotar_url_produto(url_original)
    print(f"[✓] URL limpa para Awin: {url_original}")

    endpoint = f"https://api.awin.com/publishers/{AWIN_PUBLISHER_ID}/linkbuilder/generate"
    headers = {
        "Authorization": f"Bearer {AWIN_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "advertiserId": AWIN_KABUM_ADVERTISER_ID,
        "destinationUrl": url_original,
        "shorten": True
    }

    try:
        resposta = requests.post(
            endpoint,
            params={"accessToken": AWIN_ACCESS_TOKEN},
            headers=headers,
            json=payload,
            timeout=(5, 20)
        )
        dados = resposta.json()
        print(f"Resposta Awin: {dados}")

        link = dados.get("shortUrl") or dados.get("url")
        if link:
            return link
        else:
            print(f"[!] Awin não retornou link: {dados}")
            return None

    except Exception as e:
        print(f"[X] Erro ao converter link KaBuM: {e}")
        return None


def converter_link_amazon(url_original: str) -> str:
    try:
        if _link_e_de(url_original, ['amzn.to', 'a.co', 'link.amazon']):
            resposta = requests.get(url_original, allow_redirects=True, timeout=5)
            url_original = resposta.url
            _anotar_url_produto(url_original)

        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(url_original)
        params = parse_qs(parsed.query)

        params.pop('tag', None)
        params.pop('linkCode', None)
        params.pop('linkId', None)

        params['tag'] = [AMAZON_ASSOCIATE_TAG]

        nova_query = urlencode(params, doseq=True)
        url_final = urlunparse(parsed._replace(query=nova_query))

        return url_final

    except Exception as e:
        print(f"[X] Erro ao converter link Amazon: {e}")
        return None


def converter_link_shopee(url_original: str) -> str:
    timestamp = int(time.time())

    payload_str = '{"query":"mutation{generateShortLink(input:{originUrl:\\"%s\\",subIds:[\\"telegram\\"]}){shortLink}}"}' % url_original

    fator = SHOPEE_APP_ID + str(timestamp) + payload_str + SHOPEE_SECRET
    assinatura = hashlib.sha256(fator.encode('utf-8')).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID},Timestamp={timestamp},Signature={assinatura}"
    }

    try:
        resposta = requests.post(
            "https://open-api.affiliate.shopee.com.br/graphql",
            data=payload_str,
            headers=headers,
            timeout=10
        )
        dados = resposta.json()

        shortLink = dados.get("data", {}).get("generateShortLink", {}).get("shortLink")
        if shortLink:
            return shortLink

        print(f"[!] Shopee não retornou link: {dados}")
        return None

    except Exception as e:
        print(f"[X] Erro ao converter link Shopee: {e}")
        return None


def assinar_requisicao_ali(params: dict, secret: str) -> str:
    params_ordenados = sorted(params.items())
    base = ''.join(f"{k}{v}" for k, v in params_ordenados)

    return hmac.new(
        secret.encode('utf-8'),
        base.encode('utf-8'),
        hashlib.sha256
    ).hexdigest().upper()


def converter_link_aliexpress(url_original: str) -> str:
    timestamp = str(int(time.time() * 1000))

    params = {
        "app_key":             ALIEXPRESS_APP_KEY,
        "timestamp":           timestamp,
        "sign_method":         "sha256",
        "v":                   "2.0",
        "method":              "aliexpress.affiliate.link.generate",
        "promotion_link_type": "0",
        "source_values":       url_original,
        "tracking_id":         ALIEXPRESS_TRACKING_ID,
    }

    params["sign"] = assinar_requisicao_ali(params, ALIEXPRESS_APP_SECRET)

    try:
        resposta = requests.post(
            "https://api-sg.aliexpress.com/sync",
            data=params,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
            timeout=(5, 20)
        )
        dados = resposta.json()
        print(f"Resposta AliExpress: {dados}")

        resultado = dados.get(
            "aliexpress_affiliate_link_generate_response", {}
        ).get("resp_result", {}).get("result", {})

        links = resultado.get("promotion_links", {}).get("promotion_link", [])

        if links:
            return links[0]["promotion_link"]
        else:
            print(f"[!] AliExpress não retornou link: {dados}")
            return None

    except Exception as e:
        print(f"[X] Erro ao converter link AliExpress: {e}")
        return None


def _titulo_normalizado(texto: str) -> str:
    titulo = parsear_mensagem(texto).get('titulo') or texto[:100]
    titulo_normalizado = re.sub(r'[^\w\s]', '', titulo).lower().strip()
    return re.sub(r'\s+', ' ', titulo_normalizado)


def _chave_dedup(texto: str) -> str:
    return hashlib.md5(_titulo_normalizado(texto).encode()).hexdigest()


def liberar_dedup(texto: str):
    """Desfaz a marcação de ja_foi_enviado quando a oferta NÃO chegou a ser
    postada (ex.: conversão falhou): a mesma oferta vinda do outro canal ainda
    deve ter a chance de sair."""
    cache_links.pop(_chave_dedup(texto), None)


def ja_foi_enviado(texto: str) -> bool:
    titulo_normalizado = _titulo_normalizado(texto)
    print(f"[cache] Título normalizado: '{titulo_normalizado}'")

    chave = hashlib.md5(titulo_normalizado.encode()).hexdigest()

    if chave in cache_links:
        print(f"[cache] HIT — duplicata bloqueada: '{titulo_normalizado}'")
        return True

    cache_links[chave] = True
    print(f"[cache] MISS — novo produto: '{titulo_normalizado}'")
    return False


def expandir_link_curto(url: str) -> str:
    """Segue os redirects de encurtadores genéricos e devolve a URL final da loja.
    Para qualquer outro domínio (ou em caso de erro) devolve a URL original,
    o que torna a função idempotente e segura de chamar mais de uma vez."""
    if not url or not any(d in url for d in ENCURTADORES_GENERICOS):
        return url

    try:
        resposta = requests.get(
            url,
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        print(f"[→] Encurtador expandido: {url} -> {resposta.url}")
        _anotar_url_produto(resposta.url)
        return resposta.url
    except Exception as e:
        print(f"[X] Erro ao expandir encurtador {url}: {e}")
        return url


def _link_e_de(link: str, dominios) -> bool:
    """True se o domínio do link é um destes ou subdomínio deles (www., pt.,
    s.click. ...). Compara o domínio, nunca um pedaço do texto: por substring,
    'a.co' (encurtador da Amazon) casava com "magazineluiz-a.co-m.br" e o link
    da Magalu de outro divulgador saía publicado como se fosse Amazon."""
    try:
        dominio = urlparse(link).hostname or ''
    except ValueError:
        return False
    return any(dominio == d or dominio.endswith('.' + d) for d in dominios)


def detectar_plataforma(link: str) -> str:
    # awin1.com: o converter_link_kabum expande e descarta o que não for KaBuM.
    if _link_e_de(link, ['kabum.com.br', 'tidd.ly', 'eioferta.com.br', 'ofertou.xyz', 'awin1.com']):
        return 'kabum'
    if _link_e_de(link, ['shopee.com.br', 'shope.ee']):
        return 'shopee'
    if _link_e_de(link, ['aliexpress.com']):
        return 'aliexpress'
    if _link_e_de(link, ['amazon.com.br', 'amzn.to', 'a.co', 'link.amazon']):
        return 'amazon'
    # 'mercadolivre.com' (sem .br) é o encurtador novo do ML: mercadolivre.com/sec/...
    if _link_e_de(link, ['mercadolivre.com.br', 'mercadolivre.com', 'meli.bz', 'meli.la']):
        return 'mercadolivre'
    return 'desconhecido'


def converter_link(link: str) -> str:
    link = expandir_link_curto(link)
    plataforma = detectar_plataforma(link)
    if plataforma == 'shopee':
        return converter_link_shopee(link)
    elif plataforma == 'aliexpress':
        return converter_link_aliexpress(link)
    elif plataforma == 'amazon':
        return converter_link_amazon(link)
    elif plataforma == 'kabum':
        return converter_link_kabum(link)
    elif plataforma == 'mercadolivre':
        return converter_link_meli(link)
    else:
        print(f"[!] Plataforma desconhecida: {link}")
        return


def parsear_mensagem(texto: str) -> dict:
    resultado = {
        'titulo': None,
        'preco': None,
        'cupom_codigo': None,
        'cupom_link': None,
        'link_produto': None,
        'e_cupom_avulso': False
    }

    linhas = texto.strip().split('\n')
    linhas_strip = [l.strip() for l in linhas if l.strip()]

    palavras_cupom = ['cupom de desconto', 'novo cupom', 'cupons de desconto', 'ative aqui', 'resgate aqui']
    tem_preco_produto = any(p in texto.lower() for p in ['r$', 'por:', 'por '])
    if any(p in texto.lower() for p in palavras_cupom) and not tem_preco_produto:
        resultado['e_cupom_avulso'] = True

    ignorar_titulo = re.compile(
        r'^(https?://|💰|💵|💲|🎟|✅|🔗|🔴|🛒|📢|🏆|🐈|🔔|!|por[:\s]|cupom|resgate|link[:\s]|anuncio|amazon prime)',
        re.IGNORECASE
    )
    for linha in linhas_strip:
        if not ignorar_titulo.match(linha) and 'http' not in linha and len(linha) > 5:
            resultado['titulo'] = linha
            break

    padrao_preco = re.search(
        r'(?:💰|💵|por[:\s]*)\s*R?\$?\s*([\d.,]+)',
        texto, re.IGNORECASE
    )
    if padrao_preco:
        resultado['preco'] = padrao_preco.group(0).strip()

    links_com_pos = [
        (m.start(), m.group())
        for m in re.finditer(r'https?://\S+', texto)
    ]

    padrao_codigo = re.search(
        r'(?:cupom|🎟)[:\s]+([A-Z0-9_\-]{4,30})',
        texto, re.IGNORECASE
    )
    if padrao_codigo:
        candidato = padrao_codigo.group(1).strip().upper()
        if not candidato.startswith('HTTP'):
            resultado['cupom_codigo'] = candidato

    marcadores_cupom = list(re.finditer(
        r'(cupom|resgate|🎟|ative aqui)',
        texto, re.IGNORECASE
    ))
    if marcadores_cupom and links_com_pos:
        for marcador in marcadores_cupom:
            pos_marcador = marcador.start()
            links_depois = [(pos, url) for pos, url in links_com_pos if pos > pos_marcador]
            if links_depois:
                resultado['cupom_link'] = links_depois[0][1]
                break

    ignorar_urls = ['t.me/', 'amzn.to/3Og5w0m', 'amzn.to/4lM3PHH']
    for pos, url in links_com_pos:
        if any(x in url for x in ignorar_urls):
            continue
        if url != resultado['cupom_link']:
            resultado['link_produto'] = url
            break

    if not resultado['link_produto'] and resultado['cupom_link']:
        resultado['link_produto'] = resultado['cupom_link']

    return resultado


def formatar_mensagem(texto_original: str, link_convertido: str, plataforma: str) -> str:
    dados = parsear_mensagem(texto_original)

    icone = {
        'amazon': '🛒', 'shopee': '🛍️',
        'aliexpress': '📦', 'kabum': '🖥️', 'magalu': '🏪',
    }.get(plataforma, '🔥')

    if dados['e_cupom_avulso']:
        msg = "🎟️ <b>CUPOM DE DESCONTO</b>\n\n"
        if dados['preco']:
            msg += f"💰 {dados['preco']}\n"
        if dados['cupom_codigo']:
            msg += f"✅ CUPOM: <code>{dados['cupom_codigo']}</code>\n"
        if link_convertido:
            msg += f"\n🔗 {link_convertido}"
        return msg

    titulo = dados['titulo'] or "Oferta Encontrada"
    msg = f"{icone} <b>{titulo}</b>\n\n"

    if dados['preco']:
        msg += f"💰 {dados['preco']}\n"
    if dados['cupom_codigo']:
        msg += f"✅ CUPOM: <code>{dados['cupom_codigo']}</code>\n"
    if dados['cupom_link'] and dados['cupom_link'] != dados['link_produto']:
        cupom_conv = converter_link(dados['cupom_link'])
        if cupom_conv:
            msg += f"🎟 Ativar cupom: {cupom_conv}\n"

    msg += f"\n🔗 {link_convertido}"
    return msg


def substituir_links_no_texto(texto: str):
    links_encontrados = re.findall(r'https?://\S+', texto)
    texto_final = texto

    if not links_encontrados:
        return texto_final, False

    for link in links_encontrados:
        link_alvo = expandir_link_curto(link)
        plataforma = detectar_plataforma(link_alvo)

        if plataforma == 'desconhecido':
            print(f"[!] Link ignorado (plataforma desconhecida/não suportada): {link}")
            return texto_final, False

        link_convertido = converter_link(link_alvo)

        if not link_convertido:
            print(f"[!] Falha ao converter o link da plataforma: {link}")
            return texto_final, False

        texto_final = texto_final.replace(link, link_convertido)

    return texto_final, True


def resolver_destino(url: str) -> str:
    """Destino de um link curto sem ID do produto (Shopee/AliExpress) lendo só
    o cabeçalho Location do redirect: não baixa a página. None em erro.
    Ex.: s.shopee.com.br/… → shopee.com.br/…-i.627750190.19998132816"""
    with _trava_destinos:
        if url in cache_destinos:
            return cache_destinos[url]
    try:
        resposta = requests.get(url, allow_redirects=False, stream=True, timeout=(5, 10),
                                headers={"User-Agent": "Mozilla/5.0"})
        try:
            destino = resposta.headers.get('Location')
        finally:
            resposta.close()
    except Exception as e:
        print(f"[preço] Não consegui resolver {url}: {e!r}")
        return None
    if not destino:
        return None
    destino = urljoin(url, destino)
    with _trava_destinos:
        cache_destinos[url] = destino
    return destino


def avaliar_preco_do_post(texto: str, texto_convertido: str, urls_resolvidas: list, momento: datetime = None):
    """Compara o preço da oferta com o histórico do produto (e registra no
    histórico). Os links curtos de Shopee/Ali do texto ORIGINAL só são
    resolvidos se o post tiver preço; os nossos links convertidos nunca (cada
    abertura contaria como clique no nosso afiliado)."""
    def resolver_links_curtos():
        curtos = [u for u in padrao_link.findall(texto) if _link_e_de(u, ENCURTADORES_SEM_ID)]
        return [resolver_destino(u) for u in curtos[:MAX_RESOLUCOES_POR_POST]]

    urls = urls_resolvidas + padrao_link.findall(texto) + padrao_link.findall(texto_convertido)
    avaliacao, motivo = historico_precos.avaliar_oferta(texto, urls, momento, resolver=resolver_links_curtos)
    if avaliacao is None:
        print(f"[preço] Sem avaliação ({motivo})")
    else:
        print(f"[preço] {historico_precos.descrever(avaliacao)}")
    return avaliacao


def converter_e_avaliar(texto: str, momento: datetime = None):
    """Converte os links e, na mesma thread, avalia o preço contra o histórico
    do produto. Registra mesmo se a conversão falhar (ex.: cookie do ML
    vencido): a promoção existiu. A avaliação nunca derruba o post."""
    _coleta.urls = []
    try:
        texto_convertido, houve_conversao = substituir_links_no_texto(texto)
        urls_resolvidas = list(_coleta.urls)
    finally:
        _coleta.urls = None

    avaliacao = None
    if MODO_COMENTARIO_PRECO != 'desligado':
        try:
            avaliacao = avaliar_preco_do_post(texto, texto_convertido, urls_resolvidas, momento)
        except Exception as e:
            print(f"[preço] Erro ao avaliar o preço (o post segue normal): {e!r}")
    return texto_convertido, houve_conversao, avaliacao


# ============================================================
# ESTADO PERSISTENTE — sobrevive a restarts
# ============================================================
_trava_estado = threading.Lock()


def _ler_estado() -> dict:
    try:
        with open(CAMINHO_ESTADO, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _atualizar_estado(alterar):
    """Lê, altera e grava o estado. Escrita atômica (os.replace): um crash no
    meio não corrompe o arquivo. Seguro entre threads; nunca levanta exceção."""
    with _trava_estado:
        try:
            estado = _ler_estado()
            alterar(estado)
            temporario = CAMINHO_ESTADO + '.tmp'
            with open(temporario, 'w', encoding='utf-8') as f:
                json.dump(estado, f)
            os.replace(temporario, CAMINHO_ESTADO)
        except Exception as e:
            print(f"[X] Erro ao gravar {CAMINHO_ESTADO}: {e!r}")


_ultimo_processado = None   # str(chat_id) -> maior id já tratado (carregado do disco na 1ª vez)


def _processados() -> dict:
    global _ultimo_processado
    if _ultimo_processado is None:
        _ultimo_processado = dict(_ler_estado().get('ultimo_processado', {}))
    return _ultimo_processado


def ja_processado(chat_id: int, msg_id: int) -> bool:
    """True se o post já foi tratado (antes de um restart, inclusive). Com
    catch_up=True o Telethon reentrega o que chegou depois do último estado que
    ele salvou — até ~60 s antes de um restart abrupto; sem esta checagem, esses
    posts sairiam duplicados no canal (o dedup por título vive só na memória)."""
    return msg_id <= _processados().get(str(chat_id), 0)


def marcar_processado(chat_id: int, msg_id: int):
    processados = _processados()
    if msg_id > processados.get(str(chat_id), 0):
        processados[str(chat_id)] = msg_id
        _atualizar_estado(lambda estado: estado.__setitem__('ultimo_processado', dict(processados)))


# Um post por vez: preserva a ordem e, numa VPS de 1 GB, evita várias marcas
# d'água (Pillow) em memória ao mesmo tempo durante uma rajada de ofertas.
_trava_processamento = asyncio.Lock()

# Último id de mensagem que o handler recebeu de cada canal (chat_id -> id).
# O vigia de updates compara isso com o último post real do canal.
_ultimo_id_visto = {}


def registrar_visto(chat_id: int, msg_id: int):
    if msg_id > _ultimo_id_visto.get(chat_id, 0):
        _ultimo_id_visto[chat_id] = msg_id


def post_antigo(data: datetime, agora: datetime = None) -> bool:
    """True se a oferta é velha demais para repostar (chegou via catch_up depois
    de uma parada longa: preço e cupom provavelmente já venceram)."""
    agora = agora or datetime.now(timezone.utc)
    return agora - data > IDADE_MAXIMA_POST


@client.on(events.NewMessage(chats=CANAIS_ALVO))
async def escutar_promocoes(event):
    # Registra ANTES de qualquer filtro: para o vigia, "visto" é ter chegado aqui.
    registrar_visto(event.chat_id, event.id)

    if post_antigo(event.message.date):
        print(f"[~] Post antigo ignorado (id {event.id}, de {event.message.date:%d/%m %H:%M} UTC)")
        return

    async with _trava_processamento:
        # Checado dentro da trava: o mesmo post pode chegar ao vivo e pelo catch_up.
        if ja_processado(event.chat_id, event.id):
            print(f"[~] Post {event.id} já tratado antes — ignorado")
            return
        try:
            # Prazo para o post inteiro: um await que nunca volta (ex.: resposta
            # do Telegram descartada) não pode segurar a fila para sempre.
            await asyncio.wait_for(processar_promocao(event), timeout=PRAZO_POR_POST)
        except asyncio.TimeoutError:
            print(f"[X] Post {event.id} passou de {PRAZO_POR_POST} s — desistindo e liberando a fila")
        finally:
            marcar_processado(event.chat_id, event.id)


async def processar_promocao(event):
    try:
        texto_da_mensagem = event.raw_text
        texto_da_mensagem = remover_rodape(texto_da_mensagem)

        try:
            chat = await asyncio.wait_for(event.get_chat(), timeout=30)
            nome_do_canal_origem = chat.title if hasattr(chat, 'title') else "Canal Desconhecido"
        except Exception:
            nome_do_canal_origem = "Canal Desconhecido"

        links_encontrados = padrao_link.findall(texto_da_mensagem)
        if not links_encontrados:
            return

        if not e_do_nicho(texto_da_mensagem):
            print(f"[filtro] Fora do nicho — ignorado ({nome_do_canal_origem})")
            return

        if ja_foi_enviado(texto_da_mensagem):
            print(f"[~] Duplicado ignorado ({nome_do_canal_origem})")
            return

        # A conversão faz várias requisições HTTP síncronas: roda numa thread para
        # não congelar o event loop do Telethon (pings e recebimento de updates).
        # Na mesma thread, o preço é comparado com o histórico do produto.
        texto_convertido, houve_conversao, avaliacao = await asyncio.to_thread(
            converter_e_avaliar, texto_da_mensagem, event.message.date
        )

        if not houve_conversao:
            liberar_dedup(texto_da_mensagem)
            print(f"[!] Nenhum link convertido — mensagem ignorada ({nome_do_canal_origem})")
            return

        print(f"\n[!] Oferta de: {nome_do_canal_origem}")
        print(f"Texto final:\n{texto_convertido}\n")

        # Só baixa imagem (foto, prévia de link com foto, imagem enviada como
        # arquivo). Vídeo/GIF não viram foto e só gastariam banda e disco.
        caminho_imagem = None
        arquivo = event.message.file
        if (arquivo and (arquivo.mime_type or '').startswith('image/')
                and (arquivo.size or 0) <= TAMANHO_MAXIMO_IMAGEM):
            destino = tempfile.mktemp(suffix='.jpg')
            try:
                caminho_imagem = await asyncio.wait_for(
                    client.download_media(event.message, file=destino),
                    timeout=60
                )
            except Exception as e:
                print(f"[X] Erro ao baixar imagem (o post sai só com texto): {e!r}")
                try:
                    os.remove(destino)
                except OSError:
                    pass

        message_id = await asyncio.to_thread(publicar, texto_convertido, caminho_imagem)
        await tratar_comentario_de_preco(avaliacao, message_id)

    except Exception as e:
        print(f"[X] Erro geral: {e}")


def publicar(texto: str, caminho_imagem: str = None):
    """Aplica a marca d'água e posta no canal. Devolve o id do post no canal
    (ou None se não deu para confirmar). Bloqueante (Pillow + HTTP): chamar
    fora do event loop, via asyncio.to_thread."""
    if not caminho_imagem:
        return enviar_para_meu_bot(texto)

    caminho_final = aplicar_marca_dagua(caminho_imagem)
    try:
        return enviar_para_meu_bot_com_imagem(texto, caminho_final)
    finally:
        for p in {caminho_imagem, caminho_final}:
            try:
                os.remove(p)
            except Exception:
                pass


def aplicar_marca_dagua(caminho_imagem_base: str) -> str:
    """Sobrepõe waterMaker.png no canto inferior direito e devolve o caminho
    da imagem final. À prova de falha: em qualquer erro devolve o caminho
    original, garantindo que o post ainda saia (só sem marca)."""
    try:
        with Image.open(caminho_imagem_base) as base_img:
            # JPEG grande já é decodificado reduzido; o thumbnail garante o teto
            # para qualquer formato (memória de uma VPS de 1 GB).
            base_img.draft('RGB', (LADO_MAXIMO_IMAGEM, LADO_MAXIMO_IMAGEM))
            base = base_img.convert("RGBA")
        base.thumbnail((LADO_MAXIMO_IMAGEM, LADO_MAXIMO_IMAGEM))
        with Image.open(CAMINHO_MARCA_DAGUA) as marca_img:
            marca = marca_img.convert("RGBA")

        # Redimensiona o selo para ~20% da largura, mantendo proporção
        largura = max(1, int(base.width * MARCA_DAGUA_FRACAO))
        altura = max(1, int(marca.height * (largura / marca.width)))
        marca = marca.resize((largura, altura), Image.LANCZOS)

        # Posição: canto inferior direito com margem
        margem = int(base.width * MARCA_DAGUA_MARGEM_FRACAO)
        pos = (base.width - largura - margem, base.height - altura - margem)

        # Compõe usando o alpha do próprio PNG (opacidade sólida)
        camada = Image.new("RGBA", base.size, (0, 0, 0, 0))
        camada.paste(marca, pos, marca)
        final = Image.alpha_composite(base, camada).convert("RGB")

        caminho_saida = tempfile.mktemp(suffix='.jpg')
        final.save(caminho_saida, "JPEG", quality=90)
        return caminho_saida
    except Exception as e:
        print(f"[X] Erro ao aplicar marca d'água: {e}")
        return caminho_imagem_base   # fallback: envia a imagem original


def _message_id(resposta):
    """Id da mensagem criada, tirado da resposta da Bot API (None se não vier)."""
    try:
        return resposta.json()['result']['message_id']
    except Exception:
        return None


def enviar_para_meu_bot_com_imagem(texto: str, caminho_imagem: str):
    url_api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    try:
        with open(caminho_imagem, 'rb') as foto:
            payload = {
                "chat_id": MEU_CANAL_ID,
                "caption": texto,
                "parse_mode": "HTML"
            }
            files = {"photo": foto}

            # (10, 60): o 1º valor limita a conexão E cada trecho do upload da
            # foto; o 2º, a espera pela resposta depois do upload.
            resposta = requests.post(url_api, data=payload, files=files, timeout=(10, 60))

            if resposta.status_code == 200:
                print("[✓] Postado com imagem no canal!")
                return _message_id(resposta)
            print(f"[X] Erro ao postar com imagem: {resposta.text}")
            return enviar_para_meu_bot(texto)

    except requests.ReadTimeout as e:
        # Sem fallback: o Telegram pode ter publicado a foto mesmo sem responder
        # a tempo, e reenviar como texto duplicaria o post no canal.
        print(f"[X] Timeout esperando o Telegram confirmar a foto (sem reenvio): {e}")
        return None
    except Exception as e:
        print(f"Erro ao enviar imagem: {e}")
        return enviar_para_meu_bot(texto)


def enviar_para_meu_bot(texto):
    url_api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MEU_CANAL_ID,
        "text": texto,
        "parse_mode": "HTML"
    }
    try:
        resposta = requests.post(url_api, json=payload, timeout=(5, 20))
        if resposta.status_code == 200:
            print("[✓] Postado no seu canal com sucesso!")
            return _message_id(resposta)
        print(f"[X] Erro ao postar: {resposta.text}")
    except Exception as e:
        print(f"Erro de conexão: {e}")
    return None


# ============================================================
# TERMÔMETRO DE PREÇO — reação + comentário quando a oferta está boa
# ============================================================
# Comentário pela sessão do Telethon (assina como o canal); reação pela Bot API
# (o bot que já posta no canal). A conta do Telethon é a mesma que escuta os
# canais de origem: limite por hora e suspensão automática em erro de
# permissão/flood, para um problema aqui nunca custar a escuta.
_entidade_canal = None
_comentarios_suspensos_ate = 0.0
_comentarios_recentes = deque()
_ERROS_PERMISSAO = (
    errors.SendAsPeerInvalidError, errors.ChatAdminRequiredError, errors.ChatWriteForbiddenError,
    errors.UserBannedInChannelError, errors.ChannelPrivateError, errors.ChatGuestSendForbiddenError,
)


def _bot_api(metodo: str, payload: dict):
    """Chama a Bot API e devolve o 'result' (None em erro). Nunca levanta exceção."""
    try:
        resposta = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{metodo}", json=payload, timeout=10)
    except Exception as e:
        print(f"[preço] Erro de rede em {metodo}: {e!r}")
        return None
    if resposta.status_code != 200:
        print(f"[preço] {metodo} falhou: {resposta.status_code} - {resposta.text[:200]}")
        return None
    try:
        return resposta.json().get('result', True)
    except Exception:
        return True


def reagir_ao_post(chat_id, message_id: int, emoji: str) -> bool:
    resultado = _bot_api('setMessageReaction', {
        "chat_id": chat_id, "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": emoji}],
    })
    return resultado is not None


def previa_no_admin(message_id: int, emoji: str, comentario: str) -> bool:
    """Modo teste: copia o post do canal para o chat do admin, reage na cópia e
    responde a ela com o comentário. O canal não muda nada."""
    if not ADMIN_CHAT_ID:
        print("[preço] TELEGRAM_ADMIN_ID não configurado — prévia não enviada.")
        return False
    copia = _bot_api('copyMessage', {"chat_id": ADMIN_CHAT_ID, "from_chat_id": MEU_CANAL_ID,
                                     "message_id": message_id})
    copia_id = copia.get('message_id') if isinstance(copia, dict) else None
    if not copia_id:
        return False
    reagir_ao_post(ADMIN_CHAT_ID, copia_id, emoji)
    enviado = _bot_api('sendMessage', {
        "chat_id": ADMIN_CHAT_ID, "text": comentario, "parse_mode": "HTML",
        "reply_parameters": {"message_id": copia_id},
        "link_preview_options": {"is_disabled": True},
    })
    return enviado is not None


async def _entidade_do_meu_canal():
    """InputPeer do canal de destino. Se a sessão ainda não tiver o canal em
    cache (sem access hash), percorre os diálogos uma vez para aprender."""
    global _entidade_canal
    if _entidade_canal is None:
        try:
            _entidade_canal = await asyncio.wait_for(client.get_input_entity(MEU_CANAL_ID), timeout=30)
        except ValueError:
            async def procurar():
                async for dialogo in client.iter_dialogs():
                    if dialogo.id == MEU_CANAL_ID:
                        return dialogo.input_entity
                return None
            _entidade_canal = await asyncio.wait_for(procurar(), timeout=120)
    return _entidade_canal


def _pode_comentar() -> bool:
    agora = time.monotonic()
    if agora < _comentarios_suspensos_ate:
        print("[preço] Comentários suspensos no momento — sem reação/comentário.")
        return False
    while _comentarios_recentes and agora - _comentarios_recentes[0] > 3600:
        _comentarios_recentes.popleft()
    if len(_comentarios_recentes) >= MAX_COMENTARIOS_POR_HORA:
        print(f"[preço] Limite de {MAX_COMENTARIOS_POR_HORA} comentários/hora atingido — pulando.")
        return False
    return True


async def _suspender_comentarios(motivo: str, segundos: int):
    global _comentarios_suspensos_ate
    _comentarios_suspensos_ate = time.monotonic() + segundos
    print(f"[preço] Comentários suspensos por {segundos // 3600} h: {motivo}")
    await asyncio.to_thread(
        avisar_admin_com_cooldown, 'comentario_preco',
        "⚠️ <b>Comentários de preço suspensos</b>\n\n"
        f"<code>{html.escape(motivo)}</code>\n\n"
        f"O bot tenta de novo em {segundos // 3600} h. Confira se a conta logada no bot é admin "
        "do canal e consegue comentar como o canal no grupo de discussão."
    )


async def comentar_no_post(message_id: int, texto: str) -> bool:
    """Comenta no post (grupo de discussão vinculado ao canal), assinando como
    o canal. Nunca comenta como conta pessoal."""
    canal = await _entidade_do_meu_canal()
    if canal is None:
        await _suspender_comentarios("o canal de destino não aparece nos diálogos da conta", 3600)
        return False

    ultimo_erro = None
    for espera in ESPERAS_COMENTARIO:
        await asyncio.sleep(espera)
        try:
            await asyncio.wait_for(client.send_message(
                canal, texto, comment_to=message_id, send_as=canal,
                parse_mode='html', link_preview=False, silent=True,
            ), timeout=30)
        except (errors.MsgIdInvalidError, ValueError, RuntimeError) as e:
            # O post ainda não chegou ao grupo de discussão: espera mais um pouco.
            ultimo_erro = e
            continue
        except errors.FloodWaitError as e:
            if e.seconds > 10:
                print(f"[preço] FloodWait de {e.seconds} s — comentário descartado.")
                return False
            await asyncio.sleep(e.seconds)
            ultimo_erro = e
            continue
        except errors.PeerFloodError as e:
            await _suspender_comentarios(repr(e), SUSPENSAO_PEER_FLOOD)
            return False
        except _ERROS_PERMISSAO as e:
            await _suspender_comentarios(repr(e), SUSPENSAO_COMENTARIOS)
            return False
        _comentarios_recentes.append(time.monotonic())
        print(f"[preço] 💬 comentou no post {message_id}")
        return True

    print(f"[preço] Não consegui comentar o post {message_id}: {ultimo_erro!r}")
    await asyncio.to_thread(
        avisar_admin_com_cooldown, 'comentario_preco_grupo',
        "⚠️ <b>Não consegui comentar no post</b>\n\n"
        f"<code>{html.escape(repr(ultimo_erro))}</code>\n\n"
        "O canal ainda tem um grupo de discussão (comentários) vinculado?"
    )
    return False


async def tratar_comentario_de_preco(avaliacao, message_id):
    """Depois de publicar: reage e comenta (ligado), manda a prévia ao admin
    (teste) ou só loga (sombra). O post já saiu: nunca levanta exceção."""
    try:
        comentario = historico_precos.texto_comentario(avaliacao)
        if not comentario:
            return
        if not message_id:
            print("[preço] Post sem id confirmado — sem reação/comentário.")
            return
        emoji = historico_precos.reacao(avaliacao)

        if MODO_COMENTARIO_PRECO == 'sombra':
            print(f"[preço] (sombra) comentaria no post {message_id} com {emoji}:\n{comentario}")
        elif MODO_COMENTARIO_PRECO == 'teste':
            ok = await asyncio.wait_for(
                asyncio.to_thread(previa_no_admin, message_id, emoji, comentario), timeout=PRAZO_COMENTARIO)
            print(f"[preço] 🧪 prévia do post {message_id} {'enviada' if ok else 'NÃO enviada'} ao admin")
        elif MODO_COMENTARIO_PRECO == 'ligado' and _pode_comentar():
            if not await asyncio.to_thread(reagir_ao_post, MEU_CANAL_ID, message_id, emoji):
                await asyncio.to_thread(
                    avisar_admin_com_cooldown, 'reacao_preco',
                    f"⚠️ <b>Não consegui reagir com {emoji} no post</b>\n\n"
                    "Confira se essa reação está liberada nas configurações do canal. "
                    "O comentário sai do mesmo jeito."
                )
            await asyncio.wait_for(comentar_no_post(message_id, comentario), timeout=PRAZO_COMENTARIO)
    except Exception as e:
        print(f"[preço] Erro ao reagir/comentar (o post já saiu): {e!r}")


# ============================================================
# VIGIAS — o bot não pode ficar "vivo mas parado" sem ninguém perceber
# ============================================================
def avisar_admin(texto: str) -> bool:
    """Manda um aviso (HTML) ao admin pela Bot API. Usado em caminhos de
    encerramento, então nunca levanta exceção. Devolve True se o aviso saiu."""
    if not ADMIN_CHAT_ID:
        print("[!] TELEGRAM_ADMIN_ID não configurado — aviso ao admin não enviado.")
        return False
    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_CHAT_ID, "text": texto, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[X] Erro ao avisar o admin: {e!r}")
        return False
    if resposta.status_code != 200:
        print(f"[X] Falha ao avisar o admin: {resposta.status_code} - {resposta.text[:200]}")
        return False
    return True


def avisar_admin_com_cooldown(tipo: str, texto: str):
    """Como avisar_admin, mas no máximo um aviso de cada `tipo` por AVISO_COOLDOWN.
    O horário fica em disco: num laço de restarts cada tentativa é um processo
    novo, e um controle em memória mandaria um aviso a cada ~10 s."""
    agora = time.time()
    ultimo = _ler_estado().get('avisos', {}).get(tipo, 0)
    if agora - ultimo < AVISO_COOLDOWN:
        print(f"[i] Aviso '{tipo}' ao admin silenciado (o último foi há {(agora - ultimo) / 60:.0f} min).")
        return
    if avisar_admin(texto):
        _atualizar_estado(lambda estado: estado.setdefault('avisos', {}).__setitem__(tipo, agora))


def reiniciar_processo(motivo: str):
    """Encerra na hora com código 1 para o systemd (Restart=always) subir o bot de
    novo. os._exit porque o event loop pode estar travado: um encerramento
    'educado' dependeria justamente dele. Os posts que o Telethon reentregar no
    próximo início são barrados por ja_processado()."""
    print(f"[X] {motivo} Encerrando para o systemd reiniciar o bot.", flush=True)
    avisar_admin_com_cooldown(
        'reinicio', f"🔄 <b>Bot reiniciado automaticamente</b>\n\n{html.escape(motivo)}"
    )
    os._exit(1)


def canal_atrasado(ultimo_visto: int, id_recente: int, data_recente: datetime,
                   agora: datetime, tolerancia: float = VIGIA_TOLERANCIA) -> bool:
    """True se o canal tem um post mais novo que o último que o handler recebeu
    e esse post já devia ter chegado há mais de `tolerancia` segundos."""
    return id_recente > ultimo_visto and (agora - data_recente).total_seconds() > tolerancia


async def _post_mais_recente(entidade):
    # Pula mensagens de serviço (post fixado, troca de foto...): elas não
    # disparam NewMessage e fariam o vigia achar que o bot ficou surdo.
    for msg in await client.get_messages(entidade, limit=5):
        if msg.action is None:
            return msg
    return None


async def checar_canais(entidades: dict) -> str:
    """Uma rodada do vigia de updates. Devolve a descrição do problema que indica
    bot surdo/sem conexão, ou None se está tudo em dia."""
    agora = datetime.now(timezone.utc)
    for canal in CANAIS_ALVO:
        try:
            if canal not in entidades:
                entidades[canal] = await asyncio.wait_for(client.get_input_entity(canal), timeout=60)
            msg = await asyncio.wait_for(_post_mais_recente(entidades[canal]), timeout=60)
        except (asyncio.TimeoutError, OSError) as e:
            # Sem resposta do Telegram: o mesmo sintoma do bot surdo.
            return f"sem resposta do Telegram ao consultar {canal}: {e!r}"
        except Exception as e:
            # Erro do próprio canal (conta removida, canal privado, @ trocado):
            # reiniciar não resolve — avisa e segue vigiando os outros canais.
            print(f"[vigia] ⚠️ Não consegui consultar {canal}: {e!r}")
            await asyncio.to_thread(
                avisar_admin_com_cooldown, f'canal:{canal}',
                f"⚠️ <b>Vigia não consegue ler {html.escape(canal)}</b>\n\n"
                f"<code>{html.escape(repr(e))}</code>\n\n"
                "A conta ainda é membro do canal? O @ do canal mudou?"
            )
            continue

        if not msg:
            continue
        if msg.chat_id not in _ultimo_id_visto:
            # Linha de base: o que já existia quando o bot subiu não é atraso.
            registrar_visto(msg.chat_id, msg.id)
        elif canal_atrasado(_ultimo_id_visto[msg.chat_id], msg.id, msg.date, agora):
            return f"o post {msg.id} de {canal} ({msg.date:%d/%m %H:%M} UTC) não chegou ao bot"
    return None


async def vigiar_updates():
    """Detecta o bot "surdo". Depois de uma queda de conexão seguida de uma
    rajada de 'Server replied with a wrong session ID', o Telethon pode ficar
    esperando para sempre um GetDifference cuja resposta foi descartada: a
    conexão e os pings seguem vivos, mas nenhum post chega ao handler (foi o que
    aconteceu em 29/09 e 30/09/2026: 4 h e 17 h de silêncio até um restart
    manual). Aqui buscamos o último post de cada canal direto na API e
    comparamos com o último que o handler recebeu."""
    entidades = {}
    falhas = 0
    while True:
        try:
            problema = await checar_canais(entidades)
        except Exception as e:
            problema = f"falha inesperada no vigia: {e!r}"

        if problema:
            falhas += 1
            print(f"[vigia] ⚠️ {problema} ({falhas}/{VIGIA_FALHAS_MAX})")
            if falhas >= VIGIA_FALHAS_MAX:
                reiniciar_processo(f"Updates do Telegram parados: {problema}.")
        else:
            falhas = 0

        await asyncio.sleep(VIGIA_INTERVALO)


_batimento = time.monotonic()
_loop_ja_bateu = False


async def bater_coracao():
    global _batimento, _loop_ja_bateu
    while True:
        _batimento = time.monotonic()
        _loop_ja_bateu = True
        await asyncio.sleep(BATIMENTO_INTERVALO)


def vigiar_event_loop():
    """Roda numa thread própria: se algo síncrono travar o asyncio, nenhum vigia
    async consegue agir — esta thread percebe o loop sem bater e derruba o
    processo para o systemd reiniciar."""
    while True:
        time.sleep(BATIMENTO_INTERVALO)
        parado = time.monotonic() - _batimento
        if parado > BATIMENTO_LIMITE:
            if _loop_ja_bateu:
                reiniciar_processo(f"Event loop travado há {parado:.0f} s.")
            else:
                reiniciar_processo(f"A conexão inicial com o Telegram não concluiu em {parado:.0f} s.")


def iniciar_vigia_do_loop():
    global _batimento
    _batimento = time.monotonic()
    threading.Thread(target=vigiar_event_loop, name='vigia-event-loop', daemon=True).start()


_tarefas_vigia = []


async def iniciar_vigias():
    # Guarda referência às tasks: o asyncio só mantém referência fraca a elas.
    _tarefas_vigia.append(asyncio.create_task(bater_coracao()))
    _tarefas_vigia.append(asyncio.create_task(vigiar_updates()))


def encerrar_sessao_invalida(motivo: str):
    """Sessão do Telegram revogada/não autorizada: reiniciar não resolve, só um
    login manual. Sai com SAIDA_SESSAO_INVALIDA para o systemd não entrar em loop
    (RestartPreventExitStatus=78 no deploy/bot-promo.override.conf)."""
    print(f"[X] Sessão do Telegram inválida: {motivo}", flush=True)
    avisar_admin_com_cooldown(
        'sessao_invalida',
        "⛔ <b>Bot parado: sessão do Telegram inválida</b>\n\n"
        f"<code>{html.escape(motivo)}</code>\n\n"
        "Só um novo login resolve. Na VPS: <code>sudo systemctl stop bot-promo</code>, depois "
        "<code>cd ~/bot-promo &amp;&amp; python3 observable.py</code> para refazer o login "
        "(Ctrl+C ao terminar) e <code>sudo systemctl start bot-promo</code>."
    )
    sys.exit(SAIDA_SESSAO_INVALIDA)


# def main():
#     print("=== Teste de Conversão de Links ===\n")

#     links_teste = [
#        "https://link.amazon/B04w6NHYf"
#        ]
    
#     for link in links_teste:
#         plataforma = detectar_plataforma(link)
#         link_convertido = converter_link(link)

#         print(f"Plataforma : {plataforma}")
#         print(f"Original   : {link}")
#         print(f"Convertido : {link_convertido}")
#         print("-" * 60)

# if __name__ == "__main__":
#     main()

if __name__ == "__main__":
    # Sob o systemd o stdout é um pipe com buffer em blocos: sem isso os logs
    # chegam atrasados ao journal e se perdem quando o processo morre.
    sys.stdout.reconfigure(line_buffering=True)
    # SIGTERM (stop/restart do systemd sem KillSignal=SIGINT) vira
    # KeyboardInterrupt: o run_until_disconnected desconecta salvando o estado.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    print("Iniciando o observador de múltiplos canais...")

    modo_terminal = sys.stdin.isatty()
    if not modo_terminal:
        # Já vigia a partida: o connect() inicial também pode travar.
        iniciar_vigia_do_loop()
    try:
        if modo_terminal:
            client.start()  # rodando no terminal: login interativo, se preciso
        else:
            # Sob o systemd não há teclado: pedir telefone viraria EOFError em loop.
            client.start(phone=lambda: encerrar_sessao_invalida("a sessão não está autorizada."))
        client.loop.run_until_complete(iniciar_vigias())
        if modo_terminal:
            # Só depois do login, que pode levar minutos digitando código/senha.
            iniciar_vigia_do_loop()
        # Versão síncrona de propósito: no SIGINT/SIGTERM ela desconecta salvando
        # o estado que o catch_up usa no próximo início.
        client.run_until_disconnected()
    except (errors.AuthKeyError, errors.UnauthorizedError) as e:
        encerrar_sessao_invalida(repr(e))
    except Exception as e:
        # Ex.: sem rede após as tentativas de reconexão, AuthKeyNotFound. O
        # systemd reinicia em 10 s; o admin fica sabendo (no máximo 1x/hora).
        print(f"[X] Erro fatal: {e!r}", flush=True)
        avisar_admin_com_cooldown(
            'erro_fatal',
            "❌ <b>Bot caiu com erro</b>\n\n"
            f"<code>{html.escape(repr(e))}</code>\n\n"
            "O systemd tenta subir de novo a cada 10 s."
        )
        raise
    finally:
        # Encerra os vigias sem o aviso "Task was destroyed but it is pending" a cada deploy.
        for tarefa in _tarefas_vigia:
            tarefa.cancel()
        if _tarefas_vigia and not client.loop.is_closed():
            client.loop.run_until_complete(asyncio.gather(*_tarefas_vigia, return_exceptions=True))
