"""
Histórico de preços por produto: o "termômetro" que diz se uma promoção está
boa comparada às promoções anteriores do MESMO produto (mês passado e/ou mês
atual).

Fica fora do observable.py de propósito: o importador do journal
(importar_journal.py) roda na VPS em outro processo, e importar o observable
abriria a sessão do Telethon — a mesma sessão em dois processos derruba a
produção (AuthKeyDuplicated). Por isso aqui só entra biblioteca padrão.

Guarda um AGREGADO por produto por mês (quantidade, soma, menor e maior
preço), nunca cada post: a média é soma ÷ quantidade, e cada oferta custa uma
leitura de ≤ 2 linhas e um UPSERT pela chave primária (VPS de 1 GB).
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sqlite3
import sys
import threading
import unicodedata
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, NamedTuple, Optional
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAMINHO_BANCO = os.path.join(BASE_DIR, 'historico_precos.db')
BRT = timezone(timedelta(hours=-3))   # sem horário de verão desde 2019

# Uma média mensal só vira referência com pelo menos isso de promoções.
MIN_PROMOCOES = 2
# Percentuais inteiros (piso), para o texto e a decisão sempre baterem
# (4,9% nunca aparece como "5% abaixo").
LIMIAR_BOM = 5            # ≥ 5% abaixo de uma referência → comenta
LIMIAR_EXCELENTE = 15     # ≥ 15% abaixo → "Preço excelente"
# "Menor preço desde o início de <mês>" só vale com histórico de verdade, e
# promove a "excelente" só a partir de 10% abaixo.
MIN_PROMOCOES_RECORDE = 4
LIMIAR_EXCELENTE_RECORDE = 10
TOLERANCIA_ACIMA = 5      # ≥ 5% ACIMA de alguma referência → houve promoção melhor: não comenta
LIMITE_SUSPEITO = 50      # ≥ 50% abaixo → provável preço mal lido, variante ou quantidade
DESVIO_MAXIMO_REGISTRO = 50   # observação tão fora da referência não entra na média
# Referência com maior ÷ menor acima disso é descartada: preços dispersos
# demais costumam ser variantes diferentes (8 GB × 16 GB) sob o mesmo anúncio.
DISPERSAO_MAXIMA = 1.6
RETENCAO_MESES = 13
RETENCAO_PRODUTOS_DIAS = 400
PRECO_MINIMO = 5_00            # R$ 5
PRECO_MAXIMO = 100_000_00      # R$ 100.000

OBSERVACAO = ("⚠️ Obs.: estimativa feita com as promoções que acompanhamos. "
              "O preço pode variar conforme modelo, cor e versão do produto.")
# Reação no post junto com o comentário (precisam estar liberadas no canal).
REACAO_BOM = '👍'
REACAO_EXCELENTE = '🔥'

MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
         'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']


# ============================================================
# PREÇO — o valor final que o comprador paga, lido do texto do post
# ============================================================
class Preco(NamedTuple):
    centavos: int
    linha: str
    tipo: str          # 'pix' | 'neutro' | 'cartao' (total parcelado "em 10x sem juros")


# Número no formato brasileiro: 1.299 / 1.299,90 / 2640 / 76,86, e também o
# "679.99" dos posts da KaBuM (ponto com 2 dígitos = centavos; com 3 = milhar).
# As travas laterais impedem pegar pedaço de outro número ("R$ 1 299").
_NUM = r'(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:[.,](\d{2}))?(?![.,]?\d)(?!\s\d{3}\b)'
_CANDIDATOS = [
    (re.compile(r'R\$\s*' + _NUM, re.I), 2),            # R$ 1.299 / R$4463
    (re.compile(r'\bpor\s*:?\s*' + _NUM, re.I), 1),      # POR: 1799 (sem R$)
    (re.compile(_NUM + r'\s*reais\b', re.I), 1),          # 643 reais
]

_ANUNCIO = re.compile(r'^\W*\(?\s*an[uú]ncio\s*\)?\W*$', re.I)
_PARCELADO = re.compile(r'^\W*parcelado\W*$', re.I | re.M)
# Post de cupom: "NOVO CUPOM…", "ALERTA de Cupom…", "CUPOM DE DESCONTO NA SHOPEE"
# (o título de um produto não fala de cupom; o cupom vem nas linhas de baixo).
_CUPOM_TITULO = re.compile(r'\bcupo(m|ns)\b|^lista\s+ainda\s+ativa')
_ESGOTADO = re.compile(r'\besgot(ado|ada|ou)\b')
_EXCLUSIVO = re.compile(
    r'clientes?\s+vip|v[aá]lid[oa]\s+(\w+\s+){0,2}para\s+(clientes|membros|assinantes)'
    r'|exclusiv[oa]\s+(para\s+)?(clientes|membros|assinantes)')
_ASSINATURA = re.compile(r'recorr[eê]ncia|programe\s+e\s+poupe')
_QTD_LINHA = re.compile(r'^\W*\d+\s*(unidades?|unid\.?|un\.?)\W*$', re.I)
_QTD_FRASE = re.compile(r'\b(adicione|compre|leve|coloque)\s+\d+\s+(unidades?|itens)\b')

# Contexto logo ANTES do valor que indica que ele não é o preço: preço antigo
# ("De R$"), parcela ("10x de R$"), compra mínima ("acima de R$", "OFF em R$"),
# teto ("limite R$"), valor do cupom, frete, cashback...
_ANTES_DESCARTA = re.compile(
    r'(\bde|\bcupom|\bdesconto|\beconomi\w*|\bcashback|\bfrete|\bat[eé]|\blimite|\blimitad[oa]\s+a'
    r'|\bm[ií]nimo|\bm[aá]ximo|\bcompras?|\bem|\bresgate|\bantes|\bera|\bacima)\s*:?\s*$')
# Contexto logo DEPOIS: "R$100 OFF", "R$ 30 de desconto", "R$ 89 cada".
_DEPOIS_DESCARTA = re.compile(
    r'^\s*(reais\s*)?(off\b|de\s+(desconto|cashback|volta|frete|b[oô]nus)|em\s+(cashback|moedas)'
    r'|cada\b|a\s+unidade|por\s+unidade|/\s*un\b)')
_PIX = re.compile(r'\b(pix|[àa]\s+vista|boleto)\b')
_CARTAO = re.compile(r'\b\d+\s*x\b|\bem\s+at[eé]\b|sem\s+juros|s/\s*juros|\bparcelad\w*|\bcart[aã]o|\bcr[eé]dito')


def _limpar(texto: str) -> str:
    """Minúsculas, sem emoji nem '~' (tachado), espaços normalizados."""
    texto = re.sub(r'[^\w\s$.,:%/+\-()]', ' ', texto.lower())
    return re.sub(r'\s+', ' ', texto)


def _linhas_do_post(texto: str) -> list:
    """Linhas do post sem URLs, cortadas no "(ANUNCIO)" (dali para baixo é
    propaganda do canal de origem, não a oferta)."""
    linhas = []
    for linha in (texto or '').split('\n'):
        if _ANUNCIO.match(linha):
            break
        linhas.append(re.sub(r'https?://\S+', ' ', linha))
    return linhas


def analisar_preco(texto: str):
    """Devolve (Preco, 'ok') ou (None, motivo). O valor do cupom nunca é
    subtraído: o preço do post já vem com ele aplicado."""
    linhas = _linhas_do_post(texto)
    nao_vazias = [i for i, l in enumerate(linhas) if l.strip()]
    if not nao_vazias:
        return None, 'sem_preco'
    titulo = _limpar(linhas[nao_vazias[0]]).strip()
    corpo = _limpar('\n'.join(linhas))

    if _CUPOM_TITULO.search(titulo) or _ESGOTADO.search(corpo):
        return None, 'cupom'
    if _EXCLUSIVO.search(corpo):
        return None, 'exclusivo'
    if _ASSINATURA.search(corpo):
        return None, 'assinatura'
    if any(_QTD_LINHA.match(l) for l in linhas) or _QTD_FRASE.search(corpo):
        return None, 'quantidade'
    parcelado = bool(_PARCELADO.search('\n'.join(linhas)))

    candidatos = []   # (forca, ordem, centavos, tipo, linha)
    for i in nao_vazias[1:]:               # a 1ª linha é o título: nunca tem o preço
        linha = linhas[i]
        achados = {}
        for padrao, forca in _CANDIDATOS:
            for m in padrao.finditer(linha):
                inicio = m.start(1)
                if inicio not in achados or achados[inicio][0] < forca:
                    achados[inicio] = (forca, m)
        ordenados = sorted(achados.items())
        for k, (inicio, (forca, m)) in enumerate(ordenados):
            anterior_fim = ordenados[k - 1][1][1].end() if k else 0
            proximo_inicio = ordenados[k + 1][1][1].start() if k + 1 < len(ordenados) else len(linha)
            antes = _limpar(linha[max(0, m.start() - 40):m.start()])
            depois = _limpar(linha[m.end():min(proximo_inicio, m.end() + 30)])
            if _ANTES_DESCARTA.search(antes) or _DEPOIS_DESCARTA.search(depois):
                continue
            perto = _limpar(linha[max(anterior_fim, m.start() - 25):m.start()]) + ' | ' + depois
            if _PIX.search(perto):
                tipo = 'pix'
            elif parcelado or _CARTAO.search(perto):
                tipo = 'cartao'
            else:
                tipo = 'neutro'
            centavos = int(m.group(1).replace('.', '')) * 100 + int(m.group(2) or 0)
            candidatos.append((forca, len(candidatos), centavos, tipo, linha.strip()))

    # Pix (o menor) > valor sem condição > total parcelado. "R$ 649 em 10x sem
    # juros" é o preço cheio do produto: no ML/Amazon/Shopee é o mesmo à vista.
    pix = [c for c in candidatos if c[3] == 'pix']
    por_forca = lambda c: (-c[0], c[1])
    neutros = sorted((c for c in candidatos if c[3] == 'neutro'), key=por_forca)
    cartao = sorted((c for c in candidatos if c[3] == 'cartao'), key=por_forca)
    if pix:
        escolhido = min(pix, key=lambda c: c[2])
    elif neutros:
        escolhido = neutros[0]
    elif cartao:
        escolhido = cartao[0]
    else:
        return None, 'sem_preco'

    if not PRECO_MINIMO <= escolhido[2] <= PRECO_MAXIMO:
        return None, 'fora_da_faixa'
    return Preco(escolhido[2], escolhido[4], escolhido[3]), 'ok'


def titulo_do_post(texto: str) -> Optional[str]:
    """1ª linha com texto de verdade (para o histórico ficar legível), pulando
    cabeçalhos como "⭐PARCELADO" e "3 UNIDADES"."""
    for linha in _linhas_do_post(texto):
        limpa = re.sub(r'\s+', ' ', linha).strip()
        if _PARCELADO.match(limpa) or _QTD_LINHA.match(limpa):
            continue
        if len(re.findall(r'[^\W\d_]', limpa)) >= 6:
            return limpa[:120]
    return None


# ============================================================
# IDENTIDADE DO PRODUTO — chave estável por loja
# ============================================================
def chave_do_produto(url: str) -> Optional[str]:
    """'meli:p:MLB…' (catálogo), 'meli:u:MLBU…', 'meli:i:MLB…' (anúncio),
    'amz:ASIN', 'kabum:ID', 'shopee:LOJA.ITEM' ou 'ali:ID'. None se a URL não
    for de produto (vitrine, categoria, página de cupom, encurtador...)."""
    try:
        partes = urlparse((url or '').replace('&amp;', '&'))
        host = (partes.hostname or '').lower()
    except ValueError:
        return None
    caminho = unquote(partes.path)

    def e_de(*dominios):
        return any(host == d or host.endswith('.' + d) for d in dominios)

    if e_de('mercadolivre.com.br'):
        m = re.search(r'/p/(MLB\d+)', caminho)
        if m:
            return f'meli:p:{m.group(1)}'
        m = re.search(r'/up/(MLBU\d+)', caminho)
        if m:
            return f'meli:u:{m.group(1)}'
        m = re.search(r'(?<![A-Za-z])MLB-?(\d{6,})', caminho)
        return f'meli:i:MLB{m.group(1)}' if m else None
    if e_de('amazon.com.br'):
        m = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?![A-Z0-9])', caminho, re.I)
        return f'amz:{m.group(1).upper()}' if m else None
    if e_de('kabum.com.br'):
        m = re.search(r'/produto/(\d+)', caminho)
        return f'kabum:{m.group(1)}' if m else None
    if e_de('shopee.com.br'):
        m = re.search(r'-i\.(\d+)\.(\d+)', caminho) or re.search(r'/product/(\d+)/(\d+)', caminho)
        return f'shopee:{m.group(1)}.{m.group(2)}' if m else None
    if e_de('aliexpress.com', 'aliexpress.us'):
        m = re.search(r'/item/(\d+)', caminho)
        if m:
            return f'ali:{m.group(1)}'
        ids = parse_qs(partes.query).get('productIds', [])
        if len(ids) == 1 and re.fullmatch(r'\d+', ids[0]):
            return f'ali:{ids[0]}'
    return None


_PALAVRAS_VAZIAS = {
    'para', 'com', 'sem', 'por', 'das', 'dos', 'nas', 'nos', 'uma', 'the', 'and', 'with', 'for',
    'kit', 'novo', 'nova', 'original', 'oficial', 'preto', 'preta', 'branco', 'branca', 'cor',
    'produto', 'item', 'html', 'www', 'http', 'https', 'social', 'index',
}


def _tokens(texto: str) -> set:
    sem_acento = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode().lower()
    return {
        t for t in re.split(r'[^a-z0-9]+', sem_acento)
        if t and t not in _PALAVRAS_VAZIAS
        and (len(t) >= 3 or (len(t) == 2 and any(c.isdigit() for c in t)))
    }


def slug_confere(texto: str, url: str) -> bool:
    """O nome no endereço do produto bate com o texto do post? Protege contra a
    vitrine do ML devolver outro produto (ex.: post de placa de vídeo cuja
    vitrine trouxe uma "manivela de vidro"). URL sem nome legível passa."""
    caminho = unquote(urlparse(url).path)
    slug = {
        t for t in _tokens(caminho)
        if not re.fullmatch(r'(mlbu?)?\d{5,}|b0[a-z0-9]{8}|dp|gp|jm|up', t)
    }
    if len(slug) < 3:
        return True
    comuns = slug & _tokens(re.sub(r'https?://\S+', ' ', texto or ''))
    com_modelo = any(any(c.isdigit() for c in t) for t in comuns)
    # 2 termos bastam se um deles é modelo ("rtx", "4060") ou se cobrem metade
    # do nome curto ("INSIDER CORE" × camiseta-core-insider).
    return len(comuns) >= 3 or (len(comuns) >= 2 and (com_modelo or 2 * len(comuns) >= len(slug)))


def chave_unica(urls: Iterable[str], texto: str):
    """(chave, 'ok') se as URLs apontam para UM produto e o texto bate com ele;
    senão (None, motivo)."""
    por_chave = {}
    for url in urls:
        chave = chave_do_produto(url)
        if chave:
            por_chave.setdefault(chave, []).append(url)
    if not por_chave:
        return None, 'sem_chave'
    if len(por_chave) > 1:
        return None, 'chaves_multiplas'
    chave, urls_da_chave = next(iter(por_chave.items()))
    if not chave.startswith('ali:') and not any(slug_confere(texto, u) for u in urls_da_chave):
        return None, 'slug_nao_bate'
    return chave, 'ok'


# ============================================================
# DATAS E FORMATAÇÃO
# ============================================================
def dia_e_mes_brt(momento: Optional[datetime] = None):
    momento = momento or datetime.now(timezone.utc)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    local = momento.astimezone(BRT)
    return local.strftime('%Y-%m-%d'), local.strftime('%Y-%m')


def deslocar_mes(mes: str, meses: int) -> str:
    ano, m = map(int, mes.split('-'))
    total = ano * 12 + (m - 1) + meses
    return f'{total // 12:04d}-{total % 12 + 1:02d}'


def mes_anterior(mes: str) -> str:
    return deslocar_mes(mes, -1)


def nome_do_mes(mes: str) -> str:
    return MESES[int(mes[5:7]) - 1]


def formatar_reais(centavos) -> str:
    reais, cents = divmod(int(round(centavos)), 100)
    inteiro = f'{reais:,}'.replace(',', '.')
    return f'R$ {inteiro}' if cents == 0 else f'R$ {inteiro},{cents:02d}'


# ============================================================
# AVALIAÇÃO — compara o preço com a média do mês passado e/ou do atual
# ============================================================
@dataclass(frozen=True)
class Referencia:
    mes: str
    media: float          # centavos
    n: int
    pct: int              # piso((média − preço) ÷ média × 100); positivo = abaixo da média


@dataclass(frozen=True)
class Avaliacao:
    chave: str
    centavos: int
    mes_atual: str
    status: str           # sem_historico | bom | excelente | na_media | acima | misto | suspeito
    referencias: tuple    # Referencia do mês passado (se houver) e depois a do mês atual
    menor_desde: Optional[str] = None   # mês desde cujo início este é o menor preço
    registrado: bool = False
    ja_comentado: bool = False          # produto já comentado hoje por um preço igual ou menor


def classificar(chave: str, centavos: int, mes_atual: str, agregados: dict) -> Avaliacao:
    """Função pura. `agregados`: mes → (n, soma, minimo, maximo), com o estado de
    ANTES desta oferta."""
    mes_passado = mes_anterior(mes_atual)
    referencias = []
    for mes in (mes_passado, mes_atual):
        agregado = agregados.get(mes)
        if not agregado:
            continue
        n, soma, minimo, maximo = agregado
        if n < MIN_PROMOCOES or (minimo > 0 and maximo / minimo > DISPERSAO_MAXIMA):
            continue
        media = soma / n
        referencias.append(Referencia(mes, media, n, math.floor((media - centavos) * 100 / media)))

    # "Menor preço desde o início de <mês>": primeiro tenta desde o mês passado;
    # se não for, só o mês atual. Exige MIN_PROMOCOES_RECORDE na conta, para
    # não virar troféu de quem só apareceu duas vezes.
    menor_desde = None
    for inicio in (mes_passado, mes_atual):
        meses = [m for m in (mes_passado, mes_atual) if m >= inicio and agregados.get(m)]
        if (agregados.get(inicio) and sum(agregados[m][0] for m in meses) >= MIN_PROMOCOES_RECORDE
                and all(centavos < agregados[m][2] for m in meses)):
            menor_desde = inicio
            break

    if not referencias:
        status = 'sem_historico'
    else:
        pcts = [r.pct for r in referencias]
        if max(pcts) >= LIMITE_SUSPEITO:
            status = 'suspeito'
        elif min(pcts) <= -TOLERANCIA_ACIMA:
            status = 'misto' if max(pcts) >= LIMIAR_BOM else 'acima'
        elif max(pcts) >= LIMIAR_EXCELENTE or (
                max(pcts) >= LIMIAR_EXCELENTE_RECORDE and menor_desde == mes_passado):
            status = 'excelente'
        elif max(pcts) >= LIMIAR_BOM:
            status = 'bom'
        else:
            status = 'na_media'
    return Avaliacao(chave, centavos, mes_atual, status, tuple(referencias), menor_desde)


def reacao(avaliacao: Avaliacao) -> str:
    return REACAO_EXCELENTE if avaliacao.status == 'excelente' else REACAO_BOM


def texto_comentario(avaliacao: Optional[Avaliacao]) -> Optional[str]:
    """Comentário (HTML do Telegram) explicando por que vale comprar. Só para
    ofertas boas; as linhas só aparecem quando são verdade."""
    if not avaliacao or avaliacao.status not in ('bom', 'excelente') or avaliacao.ja_comentado:
        return None
    if avaliacao.status == 'excelente':
        linhas = ['🔥 <b>Preço excelente!</b>']
    else:
        linhas = ['✅ <b>Bom momento pra comprar!</b>']
    linhas.append(f'💰 Agora: {formatar_reais(avaliacao.centavos)}')
    for ref in avaliacao.referencias:
        nome = nome_do_mes(ref.mes) + (' até aqui' if ref.mes == avaliacao.mes_atual else '')
        detalhe = f'({formatar_reais(round(ref.media / 100) * 100)} em {ref.n} promoções)'
        if ref.pct >= LIMIAR_BOM:
            linhas.append(f'📉 {ref.pct}% abaixo da média de {nome} {detalhe}')
        else:
            linhas.append(f'➖ Em linha com a média de {nome} {detalhe}')
    if avaliacao.menor_desde == mes_anterior(avaliacao.mes_atual):
        linhas.append(f'🏆 Menor preço desde o início de {nome_do_mes(avaliacao.menor_desde)}.')
    elif avaliacao.menor_desde == avaliacao.mes_atual:
        linhas.append(f'🏆 Menor preço de {nome_do_mes(avaliacao.mes_atual)} até aqui.')
    linhas.append('')
    linhas.append(f'<i>{OBSERVACAO}</i>')
    return '\n'.join(linhas)


# ============================================================
# BANCO (SQLite) — um agregado por produto por mês
# ============================================================
_DDL = """
CREATE TABLE IF NOT EXISTS precos_mes (
    chave  TEXT    NOT NULL,
    mes    TEXT    NOT NULL,       -- 'AAAA-MM' (horário de Brasília)
    n      INTEGER NOT NULL,       -- promoções contadas no mês
    soma   INTEGER NOT NULL,       -- soma dos preços (centavos)
    minimo INTEGER NOT NULL,
    maximo INTEGER NOT NULL,
    PRIMARY KEY (chave, mes)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS produtos (
    chave              TEXT PRIMARY KEY,
    titulo             TEXT,
    ultimo_dia         TEXT,
    ultimo_centavos    INTEGER,
    comentado_dia      TEXT,       -- 1 comentário por produto por dia...
    comentado_centavos INTEGER     -- ...a não ser que o preço caia mais
) WITHOUT ROWID;
"""
_bancos_prontos = set()
_ultima_poda = {}
_trava = threading.Lock()


def _conectar(caminho: Optional[str] = None) -> sqlite3.Connection:
    """Uma conexão por chamada: segura entre as threads do asyncio.to_thread e
    entre processos (o importador pode rodar com o bot no ar)."""
    caminho = caminho or CAMINHO_BANCO
    con = sqlite3.connect(caminho, timeout=5, isolation_level=None)
    if caminho not in _bancos_prontos:
        con.executescript(_DDL)
        with _trava:
            _bancos_prontos.add(caminho)
    return con


def _podar(con: sqlite3.Connection, caminho: str, dia: str, mes: str):
    """Uma vez por dia apaga o que já não serve para nenhuma comparação."""
    with _trava:
        if _ultima_poda.get(caminho) == dia:
            return
        _ultima_poda[caminho] = dia
    con.execute('DELETE FROM precos_mes WHERE mes < ?', (deslocar_mes(mes, -RETENCAO_MESES),))
    limite = (datetime.strptime(dia, '%Y-%m-%d') - timedelta(days=RETENCAO_PRODUTOS_DIAS)).strftime('%Y-%m-%d')
    con.execute('DELETE FROM produtos WHERE ultimo_dia < ?', (limite,))


def registrar_e_avaliar(chave: str, centavos: int, momento: Optional[datetime] = None,
                        titulo: Optional[str] = None, caminho: Optional[str] = None,
                        marcar_comentario: bool = True) -> Avaliacao:
    """Avalia a oferta com o histórico de ANTES dela e registra o preço no
    agregado do mês, tudo numa transação. `marcar_comentario=False` (importação
    do histórico) não conta a oferta como "já comentada hoje"."""
    caminho = caminho or CAMINHO_BANCO
    dia, mes = dia_e_mes_brt(momento)
    with closing(_conectar(caminho)) as con:
        con.execute('BEGIN IMMEDIATE')
        try:
            agregados = {
                linha[0]: tuple(linha[1:]) for linha in con.execute(
                    'SELECT mes, n, soma, minimo, maximo FROM precos_mes WHERE chave = ? AND mes IN (?, ?)',
                    (chave, mes_anterior(mes), mes))
            }
            avaliacao = classificar(chave, centavos, mes, agregados)
            produto = con.execute(
                'SELECT ultimo_dia, ultimo_centavos, comentado_dia, comentado_centavos FROM produtos '
                'WHERE chave = ?', (chave,)).fetchone() or (None, None, None, None)
            # Os 2 canais espelham a mesma oferta (e repostam): mesmo dia e mesmo
            # preço conta uma vez só.
            repetido = tuple(produto[:2]) == (dia, centavos)
            fora_da_curva = any(abs(r.pct) > DESVIO_MAXIMO_REGISTRO for r in avaliacao.referencias)
            registrar = not repetido and not fora_da_curva

            # O mesmo produto repostado no dia só ganha outro comentário se o
            # preço cair ainda mais.
            ja_comentado = False
            if avaliacao.status in ('bom', 'excelente'):
                ja_comentado = produto[2] == dia and centavos >= produto[3]
                if not ja_comentado and marcar_comentario:
                    con.execute(
                        'INSERT INTO produtos (chave, comentado_dia, comentado_centavos) VALUES (?, ?, ?) '
                        'ON CONFLICT (chave) DO UPDATE SET comentado_dia = excluded.comentado_dia, '
                        'comentado_centavos = excluded.comentado_centavos',
                        (chave, dia, centavos))
            if registrar:
                con.execute(
                    'INSERT INTO precos_mes (chave, mes, n, soma, minimo, maximo) VALUES (?, ?, 1, ?, ?, ?) '
                    'ON CONFLICT (chave, mes) DO UPDATE SET n = n + 1, soma = soma + excluded.soma, '
                    'minimo = MIN(minimo, excluded.minimo), maximo = MAX(maximo, excluded.maximo)',
                    (chave, mes, centavos, centavos, centavos))
            con.execute(
                'INSERT INTO produtos (chave, titulo, ultimo_dia, ultimo_centavos) VALUES (?, ?, ?, ?) '
                'ON CONFLICT (chave) DO UPDATE SET titulo = COALESCE(excluded.titulo, titulo), '
                'ultimo_dia = excluded.ultimo_dia, ultimo_centavos = excluded.ultimo_centavos',
                (chave, titulo, dia, centavos))
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise
        _podar(con, caminho, dia, mes)
    return replace(avaliacao, registrado=registrar, ja_comentado=ja_comentado)


def avaliar_oferta(texto: str, urls: Iterable[str], momento: Optional[datetime] = None, *,
                   resolver: Optional[Callable[[], list]] = None, caminho: Optional[str] = None,
                   marcar_comentario: bool = True):
    """Fluxo completo de uma oferta: preço → produto → registra e avalia.
    `resolver` (opcional) traz URLs extras e só é chamado se houver preço — é
    onde o bot resolve os links curtos de Shopee/AliExpress.
    Devolve (Avaliacao, 'ok') ou (None, motivo)."""
    preco, motivo = analisar_preco(texto)
    if preco is None:
        return None, motivo
    urls = list(urls)
    if resolver is not None:
        urls += [u for u in resolver() if u]
    chave, motivo = chave_unica(urls, texto)
    if chave is None:
        return None, motivo
    if preco.tipo == 'cartao' and chave.startswith('kabum:'):
        # Na KaBuM o Pix sai bem mais barato que o parcelado: misturar os dois
        # faria qualquer post com o preço do Pix parecer "abaixo da média".
        return None, 'so_cartao'
    avaliacao = registrar_e_avaliar(chave, preco.centavos, momento, titulo_do_post(texto), caminho,
                                    marcar_comentario)
    return avaliacao, 'ok'


def descrever(avaliacao: Avaliacao) -> str:
    """Resumo de uma linha para o log."""
    refs = ', '.join(
        f'{nome_do_mes(r.mes)} {formatar_reais(round(r.media))} n={r.n} ({r.pct:+d}%)'
        for r in avaliacao.referencias) or 'sem referência'
    extra = ' (já comentado hoje)' if avaliacao.ja_comentado else ''
    return f'{avaliacao.chave} {formatar_reais(avaliacao.centavos)} → {avaliacao.status}{extra} [{refs}]'


# ============================================================
# CLI — consultas rápidas na VPS
#   python3 historico_precos.py resumo
#   python3 historico_precos.py produto meli:p:MLB52170796
# ============================================================
def _resumo(caminho: Optional[str]):
    with closing(_conectar(caminho)) as con:
        linhas = con.execute(
            "SELECT mes, substr(chave, 1, instr(chave, ':') - 1) AS loja, COUNT(*), SUM(n) "
            "FROM precos_mes GROUP BY mes, loja ORDER BY mes, loja").fetchall()
        total = con.execute('SELECT COUNT(*) FROM produtos').fetchone()[0]
    print(f'{total} produtos no histórico')
    for mes, loja, produtos, promocoes in linhas:
        print(f'{mes}  {loja:<7} {produtos:>5} produtos  {promocoes:>6} promoções')


def _produto(chave: str, caminho: Optional[str]):
    with closing(_conectar(caminho)) as con:
        info = con.execute('SELECT titulo, ultimo_dia, ultimo_centavos FROM produtos WHERE chave = ?',
                           (chave,)).fetchone()
        meses = con.execute('SELECT mes, n, soma, minimo, maximo FROM precos_mes WHERE chave = ? ORDER BY mes',
                            (chave,)).fetchall()
    if not info and not meses:
        print(f'{chave}: sem histórico')
        return
    if info:
        print(f'{chave}: {info[0] or "(sem título)"} — último: {formatar_reais(info[2])} em {info[1]}')
    for mes, n, soma, minimo, maximo in meses:
        print(f'  {mes}: média {formatar_reais(soma / n)} em {n} promoções '
              f'(menor {formatar_reais(minimo)}, maior {formatar_reais(maximo)})')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Consulta o histórico de preços do bot.')
    parser.add_argument('--banco', help='caminho do banco (padrão: o do bot)')
    sub = parser.add_subparsers(dest='comando', required=True)
    sub.add_parser('resumo', help='produtos e promoções por mês e loja')
    produto = sub.add_parser('produto', help='histórico mensal de um produto')
    produto.add_argument('chave')
    args = parser.parse_args(argv)
    if args.comando == 'resumo':
        _resumo(args.banco)
    else:
        _produto(args.chave, args.banco)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
