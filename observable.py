from telethon import TelegramClient, events
from cachetools import TTLCache
import re
import requests
import hashlib
import hmac
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qsl, urlparse, parse_qs, urlencode, urlunparse, unquote
import tempfile

from dotenv import load_dotenv
import os

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
MELI_AFFILIATE_TAG = os.getenv('MELI_AFFILIATE_TAG') # O "tag" do JSON (ex: ta20250609093813)

ADMIN_CHAT_ID = os.getenv('TELEGRAM_ADMIN_ID')


CANAIS_ALVO = [
    '@PoisonPromos',
    '@OQMDVPROMO'
]

# Cache com TTL de 5 minutos e máximo de 500 entradas
# A chave será o hash do link, o valor é irrelevante (usamos True)
cache_links = TTLCache(maxsize=10, ttl=300)

client = TelegramClient('minha_sessao', API_ID, API_HASH)
padrao_link = re.compile(r'https?://\S+')

# Parâmetros de rastreio de terceiros que devem ser removidos
PARAMS_AFILIADO_TERCEIRO = {
    'aw_affid', 'awc', 'sv1', 'sv_campaign_id',
    'utm_source', 'utm_medium', 'utm_campaign',
    'utm_content', 'utm_term'
}

PALAVRAS_FORA_NICHO = {
    # Moda e vestuário
    'camisa', 'camiseta', 'blusa', 'vestido', 'calça', 'calca','condicionado','ar-condicionado','electrolux', 'tv',
    'shorts', 'cueca', 'calcinha', 'sutiã', 'sutia', 'meia',
    'tênis', 'tenis', 'sapato', 'bota', 'sandália', 'sandalia',
    'chinelo', 'tamanco', 'salto', 'mocassim',
    'bolsa', 'carteira', 'pochete', 'mochila de tecido',
    'óculos de sol', 'oculos de sol', 'relógio', 'relogio',
    'pulseira', 'colar', 'brinco', 'anel', 'cordão',
    'perfume', 'colônia', 'roupa', 'jaqueta', 'casaco', 'moletom',
    'pijama', 'bermuda', 'chapéu', 'chapeu', 'boné', 'bone',
    'gravata', 'cinto', 'lenço','smartwatch',

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
    'organizador', 'cabide', 'porta sabão','iphone', 'ipad', 'MacBook', 'Apple Watch', 'AirPods','impressora','samsung galaxy','power bank','carregador portátil','ar condicionado','ketchup','mostarda','maionese','refrigerante','suco','água mineral','agua mineral','cerveja artesanal','vinho tinto','whisky escocês','suplemento alimentar','barra de proteína','ração para cachorro','ração para gato','coleira para cachorro','cama de cachorro','arranhador para gato','aquário para peixes','gaiola para pássaros','bicicleta de estrada','bike de montanha','esteira ergométrica','elíptico doméstico','halteres ajustáveis','anilha de peso olímpica','kettlebell de ferro fundido','barra de musculação olímpica','tapete de yoga antiderrapante','aula de yoga online','natação em piscina coberta','chuteira de futebol society','bola de futebol oficial da FIFA',
    'caixa de som',
    # Ferramentas e construção
    'furadeira', 'parafusadeira', 'martelete', 'esmerilhadeira','cooktop',
    'serra', 'serrote', 'martelo', 'chave de fenda', 'alicate',
    'trena', 'nível', 'fita isolante', 'cimento', 'argamassa',
    'tinta', 'pincel', 'rolo de pintura', 'lixa', 'mangueira', 'jogo',

    # Beleza, higiene e saúde
    'shampoo', 'condicionador', 'creme de cabelo', 'máscara capilar',
    'hidratante', 'protetor solar', 'creme facial',
    'maquiagem', 'base', 'batom', 'esmalte', 'blush', 'sombra',
    'depilador', 'barbeador', 'aparelho de barbear', 'lâmina',
    'secador de cabelo', 'chapinha', 'modelador', 'prancha',
    'escova de dente', 'fio dental', 'enxaguante',
    'absorvente', 'fraldas adulto',
    'suplemento', 'whey', 'creatina', 'proteína', 'proteina','copa','fifa','powerbank'
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
    'coleira', 'guia', 'cama de cachorro', 'arranhador',
    'aquário', 'aquario', 'gaiola',

    # Esporte (não gamer)
    'bicicleta', 'bike', 'esteira', 'elíptico', 'eliptico',
    'halteres', 'halter', 'anilha', 'kettlebell', 'barra',
    'tapete de yoga', 'yoga', 'natação', 'natacao',
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
    'regador', 'pá de jardim', 'tesoura de poda','smartphone','celular', 'mochila'
}

def extrair_buy_box_winner(url: str) -> str:
    """
    Extrai o ID do anúncio real. Prioriza o ID que está dentro dos 
    parâmetros pdp_filters ou wid (comum em links de catálogo /p/).
    """
    url_corrigida = url.replace('&amp;', '&')
    parsed = urlparse(url_corrigida)
    query_params = dict(parse_qsl(parsed.query))
    
    # 1. Tenta achar o ID do vendedor específico no wid
    if 'wid' in query_params:
        match = re.search(r'MLB-?\d+', query_params['wid'])
        if match: return match.group(0).replace('-', '')
        
    # 2. Tenta achar nos filtros de catálogo
    if 'pdp_filters' in query_params:
        # Tira o %3A (dois pontos codificados) para ler limpo
        filtro_descodificado = unquote(query_params['pdp_filters'])
        match = re.search(r'MLB-?\d+', filtro_descodificado)
        if match: return match.group(0).replace('-', '')
        
    # 3. Fallback: Se não tem parâmetro, extrai do próprio caminho da URL
    match = re.search(r'MLB-?\d+', parsed.path)
    if match:
        return match.group(0).replace('-', '')
        
    return ""

def limpar_url_produto(url_suja: str) -> str:
    """
    Remove o rastreamento concorrente (matt_, tracking_id), 
    mas preserva os filtros essenciais para páginas de catálogo.
    """
    # Trata caso a URL venha com &amp; do HTML ao invés de &
    url_suja = url_suja.replace('&amp;', '&')
    
    parsed = urlparse(url_suja)
    query_params = parse_qsl(parsed.query)
    
    params_essenciais = []
    for key, value in query_params:
        # Só mantemos parâmetros que importam para o produto abrir certo
        if key in ['pdp_filters', 'wid']:
            params_essenciais.append((key, value))
            
    nova_query = urlencode(params_essenciais)
    
    # Remonta a URL
    url_limpa = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if nova_query:
        url_limpa += f"?{nova_query}"
        
    return url_limpa

def extrair_id_mlb(url: str) -> str:
    """Extrai o código do produto (ex: MLB4677093913) de dentro da URL."""
    match = re.search(r'MLB-?\d+', url.replace('-', '')) # Lida com MLB123 e MLB-123
    return match.group(0) if match else ""

def desempacotar_link(url_curta: str) -> str:
    """
    Resolve links meli.la e extrai o link final do produto.
    Se cair em uma página /social/, raspa o HTML para achar o produto.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Faz a requisição seguindo todos os redirecionamentos (resolve o meli.la)
        resposta = requests.get(url_curta, headers=headers, allow_redirects=True, timeout=10)
        url_atual = resposta.url
        
        # Limpa barras escapadas caso existam no JSON da página
        html_limpo = resposta.text.replace('\\/', '/')

        # Verifica se já chegamos no produto diretamente
        if extrair_id_mlb(url_atual) and "/social/" not in url_atual:
            return url_atual
            
        # Se a URL final for uma página de vitrine/social
        if "/social/" in url_atual:
            # Regex para pescar qualquer link de produto do Mercado Livre no HTML
            padrao = r'https://[^\s"\'<>]*mercadolivre\.com\.br/[^\s"\'<>]*MLB-?\d+[^\s"\'<>]*'
            matches = re.findall(padrao, html_limpo)
            
            if matches:
                # Retorna o primeiro link de produto encontrado na página social
                link_produto = matches[0]
                print(f"[🔎] Produto extraído da página social: {link_produto}")
                link_produto_limpo = limpar_url_produto(link_produto)  # Limpa o link antes de retornar
                print(f"[🔎] Link do produto limpo: {link_produto_limpo}")
                return link_produto_limpo
            else:
                print(f"[X] Nenhum produto encontrado dentro do link social: {url_atual}")
                return None
                
        return url_atual

    except Exception as e:
        print(f"[X] Erro ao desempacotar o link {url_curta}: {e}")
        return None

def enviar_alerta_expiracao(status_code: int):
    # ... (Mantenha a função de alerta idêntica ao código anterior)
    pass

def converter_link_meli(url_original: str) -> str:
    """Converte o link após garantir que temos a URL final do produto, sem rastros de concorrentes."""
    if not MELI_COOKIE or not MELI_X_CSRF_TOKEN or not MELI_AFFILIATE_TAG:
        print("[!] Credenciais incompletas no .env.")
        return None

    print(f"[⏳] Processando link recebido: {url_original}")
    
    # 1. DESEMPACOTA O LINK PRIMEIRO
    url_real_produto = desempacotar_link(url_original)
    
    if not url_real_produto:
        print("[!] Não foi possível chegar a um link de produto válido. Ignorando.")
        return None

    # --- NOVO PASSO: LIMPEZA ---
    # 1.5 LIMPA A URL (Remove rastreio do concorrente, mas mantém filtros de catálogo pdp_filters)
    url_limpa = limpar_url_produto(url_real_produto)

    # 2. EXTRAI O MLB (Usando a nova função inteligente na url limpa)
    id_produto = extrair_buy_box_winner(url_limpa)
    
    if not id_produto:
        print(f"[!] ID do produto (Buy Box) não encontrado na URL resolvida: {url_limpa}")
        return None
        
    print(f"[🔎] ID Buy Box Winner identificado: {id_produto}")

    # 3. SEGUE COM A CONVERSÃO NORMAL
    url_api = "https://www.mercadolivre.com.br/affiliate-program/api/v2/stripe/user/links"
    
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "cookie": MELI_COOKIE,
        "x-csrf-token": MELI_X_CSRF_TOKEN,
        "origin": "https://www.mercadolivre.com.br",
        "referer": url_limpa, # <-- IMPORTANTE: Atualizado para a URL limpa
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    payload = {
        "url": url_limpa, # <-- IMPORTANTE: O Mercado Livre agora recebe a URL virgem
        "tag": MELI_AFFILIATE_TAG,
        "buy_box_winner": id_produto # A nova função já entrega sem o traço '-'
    }

    try:
        resposta = requests.post(url_api, headers=headers, json=payload, timeout=8)
        
        if resposta.status_code == 200:
            dados = resposta.json()
            link_afiliado = dados.get("short_url") or dados.get("link") or dados.get("url")
            
            if link_afiliado:
                print(f"[✓] Sucesso! Convertido para: {link_afiliado}")
                return link_afiliado
            return None

        elif resposta.status_code in [401, 403]:
            print(f"[⚠️] Erro {resposta.status_code}: Sessão expirada ou CSRF Token inválido!")
            # enviar_alerta_expiracao(resposta.status_code) # Descomente se for usar
            return None
            
        else:
            # Adicionado resposta.text no print de erro para facilitar debugar se o ML reclamar de algo
            print(f"[X] Erro API: {resposta.status_code} - {resposta.text}") 
            return None
            
    except Exception as e:
        print(f"[X] Erro no requests: {e}")
        return None
    
def e_do_nicho(texto: str) -> bool:
    """
    Retorna False se encontrar qualquer palavra fora do nicho.
    Se não encontrar nenhuma palavra bloqueada, deixa passar.
    """
    texto_lower = texto.lower()

    for palavra in PALAVRAS_FORA_NICHO:
        if palavra in texto_lower:
            print(f"[filtro] ❌ Rejeitado — '{palavra}'")
            return False

    print(f"[filtro] ✅ Dentro do nicho")
    return True


def limpar_url_kabum(url: str) -> str:
    """Remove todos os parâmetros de afiliado de terceiros da URL da KaBuM."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        # Remove todos os parâmetros contaminados
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
    """Expande link encurtado e retorna URL limpa da KaBuM."""
    try:
        resposta = requests.get(
            url_encurtada,
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        url_expandida = resposta.url
        print(f"[→] Expandida: {url_expandida}")

        # Extrai o ?ued= se caiu em awin1.com
        if 'awin1.com' in url_expandida:
            parsed = urlparse(url_expandida)
            params = parse_qs(parsed.query)
            ued = params.get('ued', [None])[0]
            if ued:
                url_destino = unquote(ued)
                print(f"[→] Destino extraído: {url_destino}")
                # ← LIMPA aqui antes de retornar
                return limpar_url_kabum(url_destino)

        if 'kabum.com.br' in url_expandida:
            return limpar_url_kabum(url_expandida)  # ← LIMPA também

        return None

    except Exception as e:
        print(f"[X] Erro: {e}")
        return None


def converter_link_kabum(url_original: str) -> str:
    """Converte link da KaBuM para link de afiliado via API Awin."""

    dominios_encurtados = ['tidd.ly', 'eioferta.com.br', 'ofertou.xyz', 'awin1.com']
    if any(d in url_original for d in dominios_encurtados):
        print(f"[~] Link encurtado/terceiro detectado, extraindo URL original...")
        url_original = extrair_url_limpa_kabum(url_original)

    # ✅ Validação: se retornou None ou não é KaBuM, ignora silenciosamente
    if not url_original or 'kabum.com.br' not in url_original:
        print(f"[!] Link ignorado — não é KaBuM: {url_original}")
        return None

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
            json=payload
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
    """Converte link da Amazon adicionando seu tag de afiliado."""
    try:
        # Resolve URLs encurtadas (amzn.to, a.co)
        if any(d in url_original for d in ['amzn.to', 'a.co']):
            resposta = requests.get(url_original, allow_redirects=True, timeout=5)
            url_original = resposta.url

        # Limpa tags de afiliado antigos e parâmetros desnecessários
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(url_original)
        params = parse_qs(parsed.query)

        # Remove tags de outros afiliados se existirem
        params.pop('tag', None)
        params.pop('linkCode', None)
        params.pop('linkId', None)

        # Adiciona seu tag
        params['tag'] = [AMAZON_ASSOCIATE_TAG]

        nova_query = urlencode(params, doseq=True)
        url_final = urlunparse(parsed._replace(query=nova_query))

        return url_final

    except Exception as e:
        print(f"[X] Erro ao converter link Amazon: {e}")
        return None

def converter_link_shopee(url_original: str) -> str:
    """Converte link da Shopee para afiliado."""

    timestamp = int(time.time())

    # ✅ Mutation inline como string (formato exato da documentação)
    # ✅ subIds: array vazio [] ou strings simples alfanuméricas sem underscore
    payload_str = '{"query":"mutation{generateShortLink(input:{originUrl:\\"%s\\",subIds:[\\"telegram\\"]}){shortLink}}"}'  % url_original

    # ✅ SHA256(app_id + timestamp + payload + secret)
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
    """HMAC-SHA256 conforme nova plataforma AliExpress."""
    params_ordenados = sorted(params.items())
    base = ''.join(f"{k}{v}" for k, v in params_ordenados)
    
    # ✅ HMAC-SHA256: secret é a CHAVE, params são a MENSAGEM (sem envolver com secret)
    return hmac.new(
        secret.encode('utf-8'),
        base.encode('utf-8'),      # ← sem f"{secret}{base}{secret}"
        hashlib.sha256
    ).hexdigest().upper()

def converter_link_aliexpress(url_original: str) -> str:

    timestamp = str(int(time.time() * 1000))  # ✅ milissegundos

    params = {
        "app_key":             ALIEXPRESS_APP_KEY,
        "timestamp":           timestamp,         # ✅ ms, não formatado
        "sign_method":         "sha256",          # ✅ sha256, não md5
        "v":                   "2.0",
        "method":              "aliexpress.affiliate.link.generate",
        "promotion_link_type": "0",
        "source_values":       url_original,
        "tracking_id":         ALIEXPRESS_TRACKING_ID,
    }

    params["sign"] = assinar_requisicao_ali(params, ALIEXPRESS_APP_SECRET)

    try:
        resposta = requests.post(
            "https://api-sg.aliexpress.com/sync",   # ✅ nova plataforma
            data=params,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
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

def ja_foi_enviado(texto: str) -> bool:
    """
    Compara pelo título do produto extraído da mensagem.
    Mesmo produto postado por canais diferentes = duplicata.
    """
    dados = parsear_mensagem(texto)
    titulo = dados.get('titulo')

    if not titulo:
        # Fallback: se não achou título, usa o texto completo
        titulo = texto[:100]

    # Normaliza: minúsculo, sem espaços extras, sem emojis
    titulo_normalizado = re.sub(r'[^\w\s]', '', titulo).lower().strip()
    titulo_normalizado = re.sub(r'\s+', ' ', titulo_normalizado)

    print(f"[cache] Título normalizado: '{titulo_normalizado}'")

    chave = hashlib.md5(titulo_normalizado.encode()).hexdigest()

    if chave in cache_links:
        print(f"[cache] HIT — duplicata bloqueada: '{titulo_normalizado}'")
        return True

    cache_links[chave] = True
    print(f"[cache] MISS — novo produto: '{titulo_normalizado}'")
    return False

def detectar_plataforma(link: str) -> str:
    if any(d in link for d in ['kabum.com.br', 'tidd.ly', 'eioferta.com.br', 'ofertou.xyz']):
        return 'kabum'
    if any(d in link for d in ['shopee.com.br', 'shope.ee']):
        return 'shopee'
    if any(d in link for d in ['aliexpress.com', 's.click.aliexpress.com']):
        return 'aliexpress'
    if any(d in link for d in ['amazon.com.br', 'amzn.to', 'a.co']):
        return 'amazon'
    if any(d in link for d in ['mercadolivre.com.br', 'meli.bz','meli.la']):
        return 'mercadolivre'
    return 'desconhecido'

def converter_link(link: str) -> str:
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
        'e_cupom_avulso': False   # ← adicione isso
    }

    linhas = texto.strip().split('\n')
    linhas_strip = [l.strip() for l in linhas if l.strip()]

    # Detecta cupom avulso
    palavras_cupom = ['cupom de desconto', 'novo cupom', 'cupons de desconto', 'ative aqui', 'resgate aqui']
    tem_preco_produto = any(p in texto.lower() for p in ['r$', 'por:', 'por '])
    if any(p in texto.lower() for p in palavras_cupom) and not tem_preco_produto:
        resultado['e_cupom_avulso'] = True

    # Título
    ignorar_titulo = re.compile(
        r'^(https?://|💰|💵|💲|🎟|✅|🔗|🔴|🛒|📢|🏆|🐈|🔔|!|por[:\s]|cupom|resgate|link[:\s]|anuncio|amazon prime)',
        re.IGNORECASE
    )
    for linha in linhas_strip:
        if not ignorar_titulo.match(linha) and 'http' not in linha and len(linha) > 5:
            resultado['titulo'] = linha
            break

    # Preço
    padrao_preco = re.search(
        r'(?:💰|💵|por[:\s]*)\s*R?\$?\s*([\d.,]+)',
        texto, re.IGNORECASE
    )
    if padrao_preco:
        resultado['preco'] = padrao_preco.group(0).strip()

    # Todos os links com posição
    links_com_pos = [
        (m.start(), m.group())
        for m in re.finditer(r'https?://\S+', texto)
    ]

    # Cupom código
    padrao_codigo = re.search(
        r'(?:cupom|🎟)[:\s]+([A-Z0-9_\-]{4,30})',
        texto, re.IGNORECASE
    )
    if padrao_codigo:
        candidato = padrao_codigo.group(1).strip().upper()
        if not candidato.startswith('HTTP'):
            resultado['cupom_codigo'] = candidato

    # Cupom link
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

    # Link produto
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

    # Cupom avulso
    if dados['e_cupom_avulso']:
        msg = "🎟️ <b>CUPOM DE DESCONTO</b>\n\n"
        if dados['preco']:
            msg += f"💰 {dados['preco']}\n"
        if dados['cupom_codigo']:
            msg += f"✅ CUPOM: <code>{dados['cupom_codigo']}</code>\n"
        if link_convertido:
            msg += f"\n🔗 {link_convertido}"
        return msg

    # Oferta normal
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

def substituir_links_no_texto(texto: str) -> tuple[str, bool]:
    """
    Substitui os links do texto pelos links de afiliado convertidos.
    Se falhar ao converter ou não reconhecer a plataforma de qualquer link,
    aborta o processo e impede o envio da mensagem.
    """
    links_encontrados = re.findall(r'https?://\S+', texto)
    texto_final = texto
    
    # Se a mensagem não tiver nenhum link, já abortamos o envio
    if not links_encontrados:
        return texto_final, False

    for link in links_encontrados:
        plataforma = detectar_plataforma(link)

        # Regra 1: Se encontrar um link que não é de plataforma conhecida, aborta a mensagem.
        if plataforma == 'desconhecido':
            print(f"[!] Link ignorado (plataforma desconhecida/não suportada): {link}")
            return texto_final, False

        link_convertido = converter_link(link)

        # Regra 2: Se a plataforma é conhecida, mas a conversão falhou (retornou None), aborta a mensagem.
        if not link_convertido:
            print(f"[!] Falha ao converter o link da plataforma: {link}")
            return texto_final, False

        # Se passou sem erros, substitui o link original pelo de afiliado
        texto_final = texto_final.replace(link, link_convertido)

    # Se passou por TODOS os links da mensagem sem falhar, libera o envio
    return texto_final, True

@client.on(events.NewMessage(chats=CANAIS_ALVO))
async def escutar_promocoes(event):
    try:
        texto_da_mensagem = event.raw_text
        chat = await event.get_chat()
        nome_do_canal_origem = chat.title if hasattr(chat, 'title') else "Canal Desconhecido"

        links_encontrados = padrao_link.findall(texto_da_mensagem)
        if not links_encontrados:
            return

        if not e_do_nicho(texto_da_mensagem):
            print(f"[filtro] Fora do nicho — ignorado ({nome_do_canal_origem})")
            return

        if ja_foi_enviado(texto_da_mensagem):
            print(f"[~] Duplicado ignorado ({nome_do_canal_origem})")
            return

        # Substitui links pelos de afiliado
        texto_convertido, houve_conversao = substituir_links_no_texto(texto_da_mensagem)

        if not houve_conversao:
            print(f"[!] Nenhum link convertido — mensagem ignorada ({nome_do_canal_origem})")
            return

        print(f"\n[!] Oferta de: {nome_do_canal_origem}")
        print(f"Texto final:\n{texto_convertido}\n")

        # Baixa imagem se houver
        caminho_imagem = None
        if event.message.media:
            try:
                caminho_imagem = await client.download_media(
                    event.message,
                    file=tempfile.mktemp(suffix='.jpg')
                )
            except Exception as e:
                print(f"[X] Erro ao baixar imagem: {e}")


        if caminho_imagem:
            enviar_para_meu_bot_com_imagem(texto_convertido, caminho_imagem)
            try:
                os.remove(caminho_imagem)
            except:
                pass
        else:
            enviar_para_meu_bot(texto_convertido)

    except Exception as e:
        print(f"[X] Erro geral: {e}")

def enviar_para_meu_bot_com_imagem(texto: str, caminho_imagem: str):
    """Envia mensagem com foto para o canal via Bot API."""
    url_api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    try:
        with open(caminho_imagem, 'rb') as foto:
            payload = {
                "chat_id": MEU_CANAL_ID,
                "caption": texto,
                "parse_mode": "HTML"
            }
            files = {"photo": foto}

            resposta = requests.post(url_api, data=payload, files=files)

            if resposta.status_code == 200:
                print("[✓] Postado com imagem no canal!")
            else:
                print(f"[X] Erro ao postar com imagem: {resposta.text}")
                # Fallback: tenta enviar só o texto
                enviar_para_meu_bot(texto)

    except Exception as e:
        print(f"Erro ao enviar imagem: {e}")
        enviar_para_meu_bot(texto)    

def enviar_para_meu_bot(texto):
    url_api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MEU_CANAL_ID,
        "text": texto,
        "parse_mode": "HTML"
    }
    try:
        
        resposta = requests.post(url_api, json=payload)
        if resposta.status_code == 200:
            print("[✓] Postado no seu canal com sucesso!")
        else:
            print(f"[X] Erro ao postar: {resposta.text}")
    except Exception as e:
        print(f"Erro de conexão: {e}")


# def main():
#     print("=== Teste de Conversão de Links ===\n")

#     links_teste = [
#        "https://s.shopee.com.br/15XmqxhTu"
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

print("Iniciando o observador de múltiplos canais...")
with client:
   client.run_until_disconnected()


    