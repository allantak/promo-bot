"""
Semeia o histórico de preços com as ofertas que o bot já publicou, lendo o
journal da VPS — nenhuma requisição às lojas.

Para cada post publicado, o journal traz a URL do produto já resolvida pelos
conversores ("[📤] URL enviada à API: …/p/MLB…", "[✓] URL limpa para Awin:
…/produto/ID") e, depois de "Texto final:", o texto do post com o preço. A
Amazon vem com /dp/ASIN no próprio texto. Shopee e AliExpress só aparecem
como link curto: esses acumulam com o bot ao vivo.

Importa só o historico_precos (nada de observable/Telethon), então pode rodar
na VPS com o bot no ar. Rode como o usuário do bot: o banco precisa ser dele.

  journalctl -u bot-promo -o short-iso --no-pager --since 2026-09-01 \\
    | nice -n 19 python3 importar_journal.py [--banco /tmp/simulacao.db] [--relatorio] [--enviar-admin 10]
"""
import argparse
import io
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import historico_precos as hp

# 2026-10-09T12:04:22+00:00 host python3[1132395]: mensagem
_LINHA = re.compile(r'^(\S+)\s+\S+\s+([^\s\[]+)\[(\d+)\]:\s?(.*)$')
_URL = re.compile(r'''https?://[^\s'"<>{}]+''')
# Linhas de log que encerram o texto do post (o texto em si não começa assim).
_FIM_DO_TEXTO = re.compile(r'^(\[|Erro |Resposta |Traceback|Iniciando)')
MAX_LINHAS_TEXTO = 60


@dataclass
class PostDoJournal:
    momento: datetime
    texto: str
    urls: list


def ler_posts(linhas):
    """Gera um PostDoJournal por "Texto final:", em ordem cronológica.

    As URLs vêm dos logs desde o "[filtro]" do post (o início do tratamento
    de cada mensagem) mais as do próprio texto. O momento é o da 1ª linha do
    post: até 02/10/2026 o stdout ia em blocos de 8 KB e as linhas seguintes
    podiam chegar ao journal horas depois."""
    urls, inicio = [], None
    bloco, pid_bloco, momento_bloco = None, None, None

    def post_do_bloco():
        texto = '\n'.join(bloco).strip()
        return PostDoJournal(momento_bloco, texto, urls + _URL.findall(texto))

    for linha in linhas:
        m = _LINHA.match(linha.rstrip('\r\n'))
        if not m or not m.group(2).startswith('python'):
            continue
        carimbo, _, pid, msg = m.groups()
        try:
            momento = datetime.fromisoformat(carimbo)
        except ValueError:
            continue

        if bloco is not None:
            if pid == pid_bloco and len(bloco) < MAX_LINHAS_TEXTO and not _FIM_DO_TEXTO.match(msg):
                bloco.append(msg)
                continue
            yield post_do_bloco()
            bloco, urls, inicio = None, [], None

        if msg.startswith('[filtro]'):
            urls, inicio = [], momento
        elif msg.startswith('Texto final:'):
            bloco, pid_bloco, momento_bloco = [], pid, inicio or momento
        else:
            urls.extend(_URL.findall(msg))

    if bloco is not None:
        yield post_do_bloco()


def importar(posts, caminho=None):
    """Registra cada post no histórico, na ordem, avaliando com o estado de
    antes dele — a mesma conta que o bot faz ao vivo. Devolve as estatísticas
    e a lista de (post, avaliação, comentário) que teriam saído."""
    estatisticas = Counter()
    comentarios = []
    hoje = hp.dia_e_mes_brt()[0]
    for post in posts:
        estatisticas['posts'] += 1
        # Os posts de hoje entram na média, mas não contam como "já comentado
        # hoje": esses comentários nunca saíram, e o bot ao vivo deve poder comentar.
        dia = hp.dia_e_mes_brt(post.momento)[0]
        avaliacao, motivo = hp.avaliar_oferta(post.texto, post.urls, post.momento, caminho=caminho,
                                              marcar_comentario=dia < hoje)
        estatisticas[f'motivo:{motivo}'] += 1
        if avaliacao is None:
            continue
        estatisticas[f'loja:{avaliacao.chave.split(":")[0]}'] += 1
        estatisticas[f'status:{avaliacao.status}'] += 1
        if not avaliacao.registrado:
            estatisticas['nao_registrado'] += 1
        comentario = hp.texto_comentario(avaliacao)
        if comentario:
            comentarios.append((post, avaliacao, comentario))
    return estatisticas, comentarios


def imprimir_relatorio(estatisticas, comentarios, exemplos=15):
    print(f"Posts lidos do journal: {estatisticas['posts']}")
    for prefixo, titulo in (('motivo:', 'Resultado da leitura'), ('loja:', 'Ofertas avaliadas por loja'),
                            ('status:', 'Status')):
        print(f'\n{titulo}:')
        for chave, valor in sorted(estatisticas.items()):
            if chave.startswith(prefixo):
                print(f'  {chave[len(prefixo):]:<18} {valor}')
    print(f"  (não entraram na média: {estatisticas['nao_registrado']} repetidas/fora da curva)")

    por_dia = Counter(hp.dia_e_mes_brt(post.momento)[0] for post, _, _ in comentarios)
    print(f'\nComentários que teriam saído: {len(comentarios)}')
    for dia, total in sorted(por_dia.items())[-31:]:
        print(f'  {dia}  {total}')

    for post, avaliacao, comentario in comentarios[-exemplos:]:
        print('\n' + '-' * 60)
        print(f'{hp.dia_e_mes_brt(post.momento)[0]}  {hp.titulo_do_post(post.texto)}')
        print(hp.descrever(avaliacao))
        print(comentario)


def ler_credenciais(caminho_env):
    """BOT_TOKEN e TELEGRAM_ADMIN_ID do ambiente ou do .env do bot. Lê só
    CHAVE=valor: o .env não é executado (o cookie do ML tem caracteres que
    quebrariam um `source`)."""
    valores = {nome: os.environ.get(nome) for nome in ('BOT_TOKEN', 'TELEGRAM_ADMIN_ID')}
    if not all(valores.values()) and os.path.exists(caminho_env):
        with open(caminho_env, encoding='utf-8') as arquivo:
            for linha in arquivo:
                linha = linha.strip()
                if linha.startswith('export '):
                    linha = linha[len('export '):]
                if not linha or linha.startswith('#') or '=' not in linha:
                    continue
                nome, valor = linha.split('=', 1)
                nome = nome.strip()
                if nome in valores and not valores[nome]:
                    valores[nome] = valor.strip().strip('"').strip("'")
    return valores['BOT_TOKEN'], valores['TELEGRAM_ADMIN_ID']


def _achar_copia_na_discussao(chamar, grupo_id, canal_id, message_id, espera=3.0):
    """O Telegram copia cada post do canal para o grupo de discussão, e o
    comentário ("Leave a comment") é uma resposta a essa cópia. Como membro
    comum (modo privacidade), o bot não recebe a cópia; então descobre o id
    dela pela ordem das mensagens: manda uma sonda silenciosa (apagada na
    hora) e confere as mensagens logo antes dela reencaminhando-as (a
    reencaminhada diz de qual post do canal veio e também é apagada)."""
    time.sleep(espera)                     # tempo do Telegram copiar o post para o grupo
    sonda = chamar('sendMessage', {'chat_id': grupo_id, 'text': '⏳', 'disable_notification': True})
    if not isinstance(sonda, dict):
        return None
    chamar('deleteMessage', {'chat_id': grupo_id, 'message_id': sonda['message_id']})
    for candidato in range(sonda['message_id'] - 1, sonda['message_id'] - 4, -1):
        conferida = chamar('forwardMessage', {'chat_id': grupo_id, 'from_chat_id': grupo_id,
                                              'message_id': candidato, 'disable_notification': True})
        if not isinstance(conferida, dict):
            continue
        chamar('deleteMessage', {'chat_id': grupo_id, 'message_id': conferida['message_id']})
        origem = conferida.get('forward_origin') or {}
        if origem.get('message_id') == message_id and (origem.get('chat') or {}).get('id') == canal_id:
            return candidato
    return None


def enviar_exemplos_ao_admin(comentarios, quantidade, token, chat_id, espera=1.0, espera_copia=3.0):
    """Manda ao chat do admin os exemplos mais recentes como vão aparecer:
    o texto do post, a reação e o comentário. Se o chat do admin for um canal
    com grupo de discussão, o comentário vai na thread do post (como no canal
    principal); senão, como resposta ao post."""
    import requests

    def chamar(metodo, payload, timeout=15):
        try:
            resposta = requests.post(f'https://api.telegram.org/bot{token}/{metodo}', json=payload, timeout=timeout)
        except Exception as e:
            print(f'[X] {metodo}: {e!r}')
            return None
        if resposta.status_code != 200:
            print(f'[X] {metodo}: {resposta.status_code} - {resposta.text[:200]}')
            return None
        return resposta.json().get('result')

    canal = chamar('getChat', {'chat_id': chat_id}) or {}
    grupo_id = canal.get('linked_chat_id')
    if grupo_id:
        eu = chamar('getMe', {}) or {}
        membro = chamar('getChatMember', {'chat_id': grupo_id, 'user_id': eu.get('id')}) or {}
        if membro.get('status') not in ('creator', 'administrator', 'member'):
            print('[!] O bot não está no grupo de discussão do chat do admin: '
                  'o comentário vai como resposta ao post.')
            grupo_id = None

    enviados = 0
    for post, avaliacao, comentario in comentarios[-quantidade:]:
        dia = datetime.strptime(hp.dia_e_mes_brt(post.momento)[0], '%Y-%m-%d').strftime('%d/%m/%Y')
        resultado = chamar('sendMessage', {
            'chat_id': chat_id, 'text': f'🧪 Exemplo real do histórico ({dia})\n\n{post.texto}',
            'link_preview_options': {'is_disabled': True},
        })
        message_id = resultado.get('message_id') if isinstance(resultado, dict) else None
        if not message_id:
            continue
        chamar('setMessageReaction', {
            'chat_id': chat_id, 'message_id': message_id,
            'reaction': [{'type': 'emoji', 'emoji': hp.reacao(avaliacao)}],
        })
        destino, responder_a = chat_id, message_id
        if grupo_id:
            copia_id = _achar_copia_na_discussao(chamar, grupo_id, canal.get('id'), message_id, espera_copia)
            if copia_id:
                destino, responder_a = grupo_id, copia_id
            else:
                print('[!] Não achei a cópia do post no grupo de discussão: '
                      'o comentário vai como resposta no canal.')
        if chamar('sendMessage', {
            'chat_id': destino, 'text': comentario, 'parse_mode': 'HTML',
            'reply_parameters': {'message_id': responder_a},
            'link_preview_options': {'is_disabled': True},
        }) is not None:
            enviados += 1
        time.sleep(espera)    # o Telegram limita ~1 mensagem/s por chat
    return enviados


def main(argv=None, entrada=None):
    parser = argparse.ArgumentParser(description='Semeia o histórico de preços a partir do journal (stdin).')
    parser.add_argument('--banco', help='caminho do banco (padrão: o do bot)')
    parser.add_argument('--relatorio', action='store_true',
                        help='mostra motivos, status, comentários por dia e exemplos')
    parser.add_argument('--enviar-admin', type=int, default=0, metavar='N',
                        help='manda os N exemplos mais recentes ao chat do admin')
    args = parser.parse_args(argv)

    if entrada is None:
        entrada = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8', errors='replace')
    estatisticas, comentarios = importar(ler_posts(entrada), caminho=args.banco)

    if args.relatorio:
        imprimir_relatorio(estatisticas, comentarios)
    else:
        print(f"{estatisticas['posts']} posts lidos, {estatisticas['motivo:ok']} registrados no histórico, "
              f"{len(comentarios)} teriam ganhado comentário.")

    if args.enviar_admin:
        token, chat_id = ler_credenciais(os.path.join(hp.BASE_DIR, '.env'))
        if not token or not chat_id:
            print('[X] BOT_TOKEN/TELEGRAM_ADMIN_ID não encontrados — exemplos não enviados.')
            return 1
        enviados = enviar_exemplos_ao_admin(comentarios, args.enviar_admin, token, chat_id)
        print(f'{enviados} exemplos enviados ao chat do admin.')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
