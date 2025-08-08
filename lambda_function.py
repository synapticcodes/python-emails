#!/usr/bin/env python3
"""
Lambda para processar e-mails de parcelas Credilly via SendGrid.
"""

from typing import Dict, List, Optional, Tuple
import requests
from datetime import datetime, timedelta, date
import json
import logging
import time
import os
import pytz
import random

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configurações via Environment Variables
AIRTABLE_API_KEY = os.environ.get('AIRTABLE_API_KEY', 'patiRLOfTIk9sv0td.7a4659adf17b1c464b25f4a07e176a36218757391d396be5afc4d6181c3dc429')
TENEX_API_KEY_CREDILLY = os.environ.get('TENEX_API_KEY_CREDILLY', 'hm8n7A16jDPeD71SSWTKYlnmBDNv8C9J0evQaowDLaA')
TENEX_API_KEY_TURING = os.environ.get('TENEX_API_KEY_TURING', '')

# Configurações SendGrid
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
SENDGRID_FROM_EMAIL = os.environ.get('SENDGRID_FROM_EMAIL', 'no-reply@example.com')
SENDGRID_FROM_NAME = os.environ.get('SENDGRID_FROM_NAME', 'Credilly Cobrança')
SENDGRID_REPLY_EMAIL = os.environ.get('SENDGRID_REPLY_EMAIL', '')
SENDGRID_TEMPLATE_VENCEU = os.environ.get('SENDGRID_TEMPLATE_VENCEU', '')
SENDGRID_TEMPLATE_VENCE_HOJE = os.environ.get('SENDGRID_TEMPLATE_VENCE_HOJE', '')
SENDGRID_TEMPLATE_VENCE_AMANHA = os.environ.get('SENDGRID_TEMPLATE_VENCE_AMANHA', '')
SENDGRID_TEMPLATE_FIELD_MAP = os.environ.get('SENDGRID_TEMPLATE_FIELD_MAP', '')  # JSON opcional p/ mapear chaves

# Observabilidade de conteúdo: BCC opcional para arquivamento/validação
BCC_ARQUIVO_EMAIL = os.environ.get('BCC_ARQUIVO_EMAIL', '')
BCC_SAMPLE_PERCENT = float(os.environ.get('BCC_SAMPLE_PERCENT', '0'))  # 0 = desabilitado; 100 = todos

# Modo teste: controlado por env var. Produção por padrão.
MODO_TESTE = os.environ.get('MODO_TESTE', 'false').lower() == 'true'
PROCESSAR_CREDILLY = True
# Habilite Turing via ENV: PROCESSAR_TURING=true
PROCESSAR_TURING = os.environ.get('PROCESSAR_TURING', 'true').lower() == 'true'
LIMITE_VENCIDAS, LIMITE_HOJE, LIMITE_AMANHA = None, None, None
# Horário permitido (padrão 9-20) com possibilidade de override por ENV
HORARIO_INICIO = int(os.environ.get('HORARIO_INICIO', '9'))
HORARIO_FIM = int(os.environ.get('HORARIO_FIM', '20'))
PAUSAR_ENTRE_ENVIO = float(os.environ.get('PAUSAR_ENTRE_ENVIO', '0.05'))
NOTIFICATION_FINALIZADO_URL = "https://api.pushcut.io/-KVMKI_4PP5GMnuH0M9oz/notifications/Envio_Email_Finalizado"
AIRTABLE_BASE_ID = 'app3SiNzJv7q5BDkV'
CLIENTES_TABLE_ID = 'tbl8YhBey4l9cOqLT'
TENEX_URL_CREDILLY = "https://credilly.tenex.com.br/api/v2/vendas/"
TENEX_URL_TURING = "https://turing.tenex.com.br/api/v2/vendas/"
AIRTABLE_BASE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"

# Supabase (logs)
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')  # service role ou anon conforme sua política

# Headers
headers_airtable = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}
headers_sendgrid = {"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"}

STATUS_PENDENTES = [1, 3, 5]

def send_notification(url: str):
    response = requests.get(url)
    if response.status_code == 200:
        logging.info(f"✅ Notificação enviada para {url}")
    else:
        logging.error(f"❌ Erro ao enviar notificação: {response.status_code} - {response.text}")

def verificar_horario_permitido() -> bool:
    tz = pytz.timezone('America/Sao_Paulo')
    hora_atual = datetime.now(tz).hour
    return HORARIO_INICIO <= hora_atual < HORARIO_FIM

def formatar_valor_moeda(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_data_brasileira(data_iso: str) -> str:
    try:
        data = datetime.strptime(data_iso, '%Y-%m-%d')
        return data.strftime('%d/%m/%Y')
    except:
        return data_iso

def montar_email_sendgrid(email_destino: str, nome_destino: str, dados: Dict, tipo: str) -> Dict:
    """
    Monta o payload para SendGrid. Usa template se configurado; caso contrário, usa conteúdo texto simples.
    """
    template_map = {
        'venceu_ontem': SENDGRID_TEMPLATE_VENCEU,
        'vence_hoje': SENDGRID_TEMPLATE_VENCE_HOJE,
        'vence_amanha': SENDGRID_TEMPLATE_VENCE_AMANHA,
    }

    status_label = 'venceu ontem' if tipo == 'venceu_ontem' else 'vence hoje' if tipo == 'vence_hoje' else 'vence amanhã'
    valor_formatado = formatar_valor_moeda(float(dados.get('valor', 0) or 0))
    subject_map = {
        'venceu_ontem': f"Parcela vencida - ação necessária ({valor_formatado})",
        'vence_hoje': f"Lembrete: sua parcela vence hoje ({valor_formatado})",
        'vence_amanha': f"Lembrete: sua parcela vence amanhã ({valor_formatado})",
    }
    subject_text = subject_map[tipo]

    template_id = template_map.get(tipo) or ''
    personalization = {
        "to": [{"email": email_destino, "name": nome_destino}],
    }

    payload: Dict = {
        "from": {"email": SENDGRID_FROM_EMAIL, "name": SENDGRID_FROM_NAME},
        "personalizations": [personalization],
    }

    if SENDGRID_REPLY_EMAIL:
        payload["reply_to"] = {"email": SENDGRID_REPLY_EMAIL, "name": SENDGRID_FROM_NAME}

    # Se configurado, adiciona BCC para arquivamento/validação (com amostragem)
    try:
        if BCC_ARQUIVO_EMAIL and BCC_SAMPLE_PERCENT > 0:
            if (BCC_SAMPLE_PERCENT >= 100) or ((random.random() * 100.0) < BCC_SAMPLE_PERCENT):
                personalization["bcc"] = [{"email": BCC_ARQUIVO_EMAIL}]
                # Inclui header para identificar o destinatário original na caixa de arquivamento
                payload.setdefault("headers", {})
                payload["headers"]["X-Original-To"] = email_destino
    except Exception:
        pass

    if template_id:
        payload["template_id"] = template_id

        # Dados canônicos enviados pelo código
        canonical_data = {
            "nome": nome_destino,
            "cliente": dados.get('cliente', nome_destino),
            "valor_parcela": valor_formatado,
            "data_vencimento": dados.get('data_vencimento'),
            "link_pagamento": dados.get('link_pagamento'),
            "status_vencimento": status_label,
            # Adiciona subject para permitir subject dinâmico no template (ex.: Subject: {{subject}})
            "subject": subject_text,
            "assunto": subject_text,
        }

        # Mapeamento opcional via env: SENDGRID_TEMPLATE_FIELD_MAP (JSON)
        mapped_data = {}
        try:
            # Mapeamento padrão para placeholders do template fornecido pelo usuário
            default_field_map = {
                "nome": "nome",
                "valor_parcela": "valor",
                "data_vencimento": "vencimento",
                "link_pagamento": "link",
                "subject": "subject",
                "assunto": "assunto",
            }
            # Se houver map customizado via env, prevalece
            field_map = json.loads(SENDGRID_TEMPLATE_FIELD_MAP) if SENDGRID_TEMPLATE_FIELD_MAP else default_field_map
            if isinstance(field_map, dict) and field_map:
                for key, value in canonical_data.items():
                    target_key = field_map.get(key, key)
                    mapped_data[target_key] = value
            else:
                mapped_data = canonical_data
        except Exception as e:
            logging.warning(f"[TEMPLATE_MAP] JSON inválido em SENDGRID_TEMPLATE_FIELD_MAP: {str(e)}. Usando nomes canônicos.")
            mapped_data = canonical_data

        personalization["dynamic_template_data"] = mapped_data

        # Define assunto mesmo usando template (ajuda quando o template não define subject)
        try:
            personalization["subject"] = subject_text
        except Exception:
            pass
        # Também define no nível raiz para aumentar compatibilidade
        try:
            payload["subject"] = subject_text
        except Exception:
            pass
    else:
        subject = subject_map[tipo]
        body_lines = [
            f"Olá {nome_destino},",
            "",
            f"Identificamos que sua parcela {status_label}.",
            f"- Valor: {valor_formatado}",
            f"- Vencimento: {dados.get('data_vencimento')}",
            f"- Link para pagamento: {dados.get('link_pagamento') or '—'}",
            "",
            "Se já realizou o pagamento, desconsidere este e-mail.",
            "",
            "Atenciosamente,",
            SENDGRID_FROM_NAME,
        ]
        payload["personalizations"][0]["subject"] = subject
        payload["content"] = [{"type": "text/plain", "value": "\n".join(body_lines)}]

    return payload


def enviar_email_sendgrid(payload: Dict) -> Tuple[bool, Optional[int], Optional[str], Optional[str]]:
    """Envia o e-mail via SendGrid. Retorna (sucesso, status_code, message_id, error_message)."""
    if MODO_TESTE:
        logging.info("[TESTE] Envio SendGrid simulado: assunto/templatedata prontos.")
        return True, 202, "TEST-MSG-ID", None

    if not SENDGRID_API_KEY:
        logging.error("SENDGRID_API_KEY não configurada.")
        return False

    url = "https://api.sendgrid.com/v3/mail/send"

    # Política simples de retry para 429/5xx
    tentativas = 0
    atraso = 1.0
    while tentativas < 5:
        try:
            response = requests.post(url, headers=headers_sendgrid, json=payload, timeout=30)
            if response.status_code == 202:
                # SendGrid normalmente não retorna body; tentar header X-Message-Id
                msg_id = response.headers.get('X-Message-Id') or response.headers.get('X-Message-ID')
                return True, response.status_code, msg_id, None
            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get('Retry-After')
                espera = float(retry_after) if retry_after and retry_after.isdigit() else atraso
                logging.warning(f"SendGrid {response.status_code}. Retentando em {espera:.1f}s...")
                time.sleep(espera)
                tentativas += 1
                atraso *= 2
                continue
            logging.error(f"Falha ao enviar e-mail: {response.status_code} - {response.text}")
            return False, response.status_code, None, response.text
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logging.warning(f"Exceção de rede no envio: {str(e)}. Retentando em {atraso:.1f}s...")
            time.sleep(atraso)
            tentativas += 1
            atraso *= 2
        except Exception as e:
            logging.error(f"Erro inesperado no envio: {str(e)}")
            return False, None, None, str(e)
    logging.error("Excedido número máximo de tentativas no SendGrid.")
    return False, None, None, "max_retries_exceeded"


def log_disparo_supabase(record: Dict) -> None:
    """Insere um registro de log no Supabase. Silencioso em caso de erro."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logging.info("[LOG] SUPABASE_URL/SUPABASE_KEY ausentes; pulando registro de log.")
        return
    try:
        url = f"{SUPABASE_URL}/rest/v1/email_disparo_logs"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        resp = requests.post(url, headers=headers, data=json.dumps(record), timeout=20)
        if resp.status_code not in (200, 201, 204):
            snippet = resp.text[:300] if resp.text else ""
            logging.warning(f"[LOG] Falha ao inserir log no Supabase: {resp.status_code} {snippet}")
    except Exception as e:
        logging.warning(f"[LOG] Exceção ao registrar log no Supabase: {str(e)}")

def buscar_todos_clientes_airtable() -> Dict[str, Dict]:
    logging.info("📥 Buscando clientes do Airtable...")
    clientes_dict = {}
    offset = None
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        url = f"{AIRTABLE_BASE_URL}/{CLIENTES_TABLE_ID}"
        response = requests.get(url, headers=headers_airtable, params=params)
        if response.status_code == 200:
            data = response.json()
            records = data.get("records", [])
            for record in records:
                fields = record['fields']
                id_credilly = fields.get('ID Credilly', '')
                if id_credilly:
                    clientes_dict[f"CRED-{id_credilly}"] = record
                id_turing = fields.get('ID Turing', '')
                if id_turing:
                    clientes_dict[f"TUR-{id_turing}"] = record
            if "offset" not in data:
                break
            offset = data["offset"]
        else:
            logging.error(f"❌ Erro ao buscar clientes: {response.text}")
            break
    logging.info(f"✅ {len(clientes_dict)} IDs de clientes indexados")
    return clientes_dict

def fetch_tenex_lote(url, api_key, params):
    logging.info(f"Tentando requisição para {url} com params: {params}")
    tentativas = 0
    atraso = 1.0
    while tentativas < 5:
        try:
            response = requests.get(url, auth=(api_key, ''), params=params, timeout=180)
            logging.info(f"Resposta recebida: {response.status_code}")
            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logging.warning(f"Falha na requisição Tenex: {str(e)}. Retentando em {atraso:.1f}s...")
            time.sleep(atraso)
            tentativas += 1
            atraso *= 2
        except Exception as e:
            logging.error(f"Erro inesperado na requisição Tenex: {str(e)}")
            return None
    logging.error("Excedido número máximo de tentativas na Tenex.")
    return None

def buscar_parcelas_por_periodo(clientes_dict: Dict[str, Dict], sistema: str) -> Dict[str, List[Tuple[Dict, Dict, str]]]:
    if sistema == 'credilly':
        url = TENEX_URL_CREDILLY
        api_key = TENEX_API_KEY_CREDILLY
        prefixo = "CRED"
    elif sistema == 'turing':
        url = TENEX_URL_TURING
        api_key = TENEX_API_KEY_TURING
        prefixo = "TUR"
    else:
        raise ValueError("sistema inválido. Use 'credilly' ou 'turing'")
    logging.info(f"🔍 Buscando parcelas no sistema {sistema.upper()}...")
    hoje = datetime.now().date()
    ontem = hoje - timedelta(days=1)
    amanha = hoje + timedelta(days=1)
    parcelas_por_periodo = {"venceu_ontem": [], "vence_hoje": [], "vence_amanha": []}
    ids_sistema = []
    for key, cliente in clientes_dict.items():
        if key.startswith(prefixo):
            id_cliente = key.replace(f"{prefixo}-", "")
            ids_sistema.append((id_cliente, cliente))
    logging.info(f"  → {len(ids_sistema)} clientes para verificar no {sistema}")
    for i in range(0, len(ids_sistema), 100):
        lote = ids_sistema[i:i+100]
        params = [("id_cliente", id_cliente) for id_cliente, _ in lote]
        logging.info(f"Processando lote {i//100+1} de {len(ids_sistema)//100 + 1}")
        try:
            response = fetch_tenex_lote(url, api_key, params)
            if response is None:
                logging.warning(f"Lote {i//100+1} ignorado devido a falha na API")
                continue
            if response.status_code == 200:
                vendas = response.json().get("data", [])
                logging.info(f"Resposta JSON: {vendas[:2]}...")
            else:
                logging.error(f"❌ Erro ao buscar lote {i//100+1}: {response.status_code}")
                continue
            for venda in vendas:
                id_cliente = str(venda.get("id_cliente", ""))
                parcelas = venda.get("parcelas", [])
                cliente_key = f"{prefixo}-{id_cliente}"
                cliente = clientes_dict.get(cliente_key)
                if not cliente:
                    continue
                for parcela in parcelas:
                    vencimento_str = parcela.get("data_vencimento", "")
                    status = parcela.get("status", 0)
                    if status not in STATUS_PENDENTES:
                        continue
                    try:
                        vencimento = datetime.strptime(vencimento_str, '%Y-%m-%d').date()
                    except:
                        continue
                    if vencimento == ontem:
                        parcelas_por_periodo["venceu_ontem"].append((parcela, cliente, id_cliente, sistema))
                    elif vencimento == hoje:
                        parcelas_por_periodo["vence_hoje"].append((parcela, cliente, id_cliente, sistema))
                    elif vencimento == amanha:
                        parcelas_por_periodo["vence_amanha"].append((parcela, cliente, id_cliente, sistema))
        except Exception as e:
            logging.error(f"❌ Erro ao processar lote {i//100+1} após retries: {str(e)}")
        if i + 100 < len(ids_sistema):
            time.sleep(0.1)
    for periodo, parcelas in parcelas_por_periodo.items():
        logging.info(f"  → {len(parcelas)} parcelas em '{periodo}'")
    return parcelas_por_periodo

def processar_parcelas_periodo(parcelas: List[Tuple[Dict, Dict, str, str]], tipo: str, limite: Optional[int]) -> Dict:
    stats = {"total": len(parcelas), "enviados": 0, "ja_enviados": 0, "sem_email": 0, "erros": 0}
    if limite:
        parcelas = parcelas[:limite]
        logging.info(f"📋 Limitando {tipo} a {limite} e-mails")

    for item in parcelas:
        # Backward-compat: alguns itens antigos podem não ter sistema; default credilly
        if len(item) == 3:
            parcela, cliente, cliente_id = item
            sistema_origem = 'credilly'
        else:
            parcela, cliente, cliente_id, sistema_origem = item
        nome = cliente['fields'].get('Nome do cliente', 'Sem nome')
        email = cliente['fields'].get('Email', '')
        if not email:
            logging.warning(f"⚠️ Cliente {nome} sem e-mail, ID Airtable: {cliente.get('id', 'desconhecido')}")
            stats["sem_email"] += 1
            # log sem_email
            log_disparo_supabase({
                "sistema": sistema_origem,
                "periodo": tipo,
                "cliente_airtable_id": cliente.get('id'),
                "cliente_sistema_id": cliente_id,
                "nome": nome,
                "email": None,
                "valor_parcela": float(parcela.get("valor", 0) or 0),
                "data_vencimento": parcela.get('data_vencimento'),
                "link_pagamento": parcela.get("pdf_url", ""),
                "status": "sem_email",
                "sendgrid_status": None,
                "sendgrid_message_id": None,
                "error_message": "cliente_sem_email",
                "request_payload": None,
            })
            continue

        vencimento_str = parcela.get('data_vencimento', '')
        try:
            dados = {
                'cliente': nome,
                'valor': parcela.get("valor", 0),
                'data_vencimento': formatar_data_brasileira(vencimento_str),
                'link_pagamento': parcela.get("pdf_url", ""),
                'status': 'venceu ontem' if tipo == 'venceu_ontem' else 'hoje' if tipo == 'vence_hoje' else 'amanhã',
            }

            payload = montar_email_sendgrid(email, nome, dados, tipo)
            sucesso, status_code, message_id, error_message = enviar_email_sendgrid(payload)
            if sucesso:
                stats["enviados"] += 1
            else:
                stats["erros"] += 1

            # log envio/erro
            log_disparo_supabase({
                "sistema": sistema_origem,
                "periodo": tipo,
                "cliente_airtable_id": cliente.get('id'),
                "cliente_sistema_id": cliente_id,
                "nome": nome,
                "email": email,
                "valor_parcela": float(parcela.get("valor", 0) or 0),
                "data_vencimento": parcela.get('data_vencimento'),
                "link_pagamento": parcela.get("pdf_url", ""),
                "status": "enviado" if sucesso else "erro",
                "sendgrid_status": status_code,
                "sendgrid_message_id": message_id,
                "error_message": error_message,
                "bcc_aplicado": bool(BCC_ARQUIVO_EMAIL and BCC_SAMPLE_PERCENT and BCC_SAMPLE_PERCENT > 0),
                "bcc_email": BCC_ARQUIVO_EMAIL or None,
                "bcc_sample_percent": BCC_SAMPLE_PERCENT if (BCC_SAMPLE_PERCENT and BCC_SAMPLE_PERCENT > 0) else None,
                "request_payload": {
                    "tipo": tipo,
                    "assunto_ou_template": payload.get('template_id') or payload.get('personalizations', [{}])[0].get('subject'),
                },
            })
        except Exception as e:
            logging.error(f"❌ Erro ao processar parcela: {str(e)}")
            stats["erros"] += 1
            # log erro inesperado
            log_disparo_supabase({
                "sistema": sistema_origem,
                "periodo": tipo,
                "cliente_airtable_id": cliente.get('id'),
                "cliente_sistema_id": cliente_id,
                "nome": nome,
                "email": email,
                "valor_parcela": float(parcela.get("valor", 0) or 0),
                "data_vencimento": parcela.get('data_vencimento'),
                "link_pagamento": parcela.get("pdf_url", ""),
                "status": "erro",
                "sendgrid_status": None,
                "sendgrid_message_id": None,
                "error_message": str(e),
                "request_payload": None,
            })

        if PAUSAR_ENTRE_ENVIO > 0:
            time.sleep(PAUSAR_ENTRE_ENVIO)

    return stats

def enviar_teste_template_unico(email_destino: str, tipo: str) -> None:
    """Envia um único e-mail usando o fluxo de template, sem Airtable/Tenex."""
    nome = "Teste"
    dados = {
        'cliente': nome,
        'valor': 123.45,
        'data_vencimento': formatar_data_brasileira(datetime.now().strftime('%Y-%m-%d')),
        'link_pagamento': 'https://exemplo.com/pagar',
        'status': 'venceu ontem' if tipo == 'venceu_ontem' else 'hoje' if tipo == 'vence_hoje' else 'amanhã',
    }
    payload = montar_email_sendgrid(email_destino, nome, dados, tipo)
    sucesso, status_code, message_id, error_message = enviar_email_sendgrid(payload)
    log_disparo_supabase({
        "sistema": "credilly",
        "periodo": tipo,
        "cliente_airtable_id": None,
        "cliente_sistema_id": None,
        "nome": nome,
        "email": email_destino,
        "valor_parcela": float(dados['valor']),
        "data_vencimento": datetime.now().strftime('%Y-%m-%d'),
        "link_pagamento": dados['link_pagamento'],
        "status": "enviado" if sucesso else "erro",
        "sendgrid_status": status_code,
        "sendgrid_message_id": message_id,
        "error_message": error_message,
        "bcc_aplicado": bool(BCC_ARQUIVO_EMAIL and BCC_SAMPLE_PERCENT and BCC_SAMPLE_PERCENT > 0),
        "bcc_email": BCC_ARQUIVO_EMAIL or None,
        "bcc_sample_percent": BCC_SAMPLE_PERCENT if (BCC_SAMPLE_PERCENT and BCC_SAMPLE_PERCENT > 0) else None,
        "request_payload": {
            "tipo": tipo,
            "assunto_ou_template": payload.get('template_id') or payload.get('personalizations', [{}])[0].get('subject'),
        },
    })
    if sucesso:
        logging.info(f"[TESTE-UNICO] Enviado com sucesso para {email_destino} (tipo={tipo}).")
    else:
        logging.error(f"[TESTE-UNICO] Falha ao enviar para {email_destino}: {error_message}")


def enviar_teste_com_dados_reais(email_destino: str, periodo_preferencial: Optional[str] = None) -> None:
    """Busca dados reais (Airtable + Tenex), escolhe uma parcela e envia para o email_destino."""
    clientes_dict = buscar_todos_clientes_airtable()
    if not clientes_dict:
        logging.error("[TESTE-REAIS] Nenhum cliente encontrado no Airtable")
        return

    parcelas_por_periodo = buscar_parcelas_por_periodo(clientes_dict, 'credilly')

    ordem = [periodo_preferencial] if periodo_preferencial in ("venceu_ontem", "vence_hoje", "vence_amanha") else ["vence_hoje", "venceu_ontem", "vence_amanha"]
    escolhido = None
    tipo_escolhido = None
    for tipo in ordem:
        lista = parcelas_por_periodo.get(tipo, [])
        if lista:
            escolhido = random.choice(lista)
            tipo_escolhido = tipo
            break
    if not escolhido:
        logging.error("[TESTE-REAIS] Não há parcelas disponíveis em ontem/hoje/amanhã")
        return

    parcela, cliente, cliente_id = escolhido
    nome = cliente['fields'].get('Nome do cliente', 'Sem nome')
    vencimento_str = parcela.get('data_vencimento', '')
    dados = {
        'cliente': nome,
        'valor': parcela.get("valor", 0),
        'data_vencimento': formatar_data_brasileira(vencimento_str),
        'link_pagamento': parcela.get("pdf_url", ""),
        'status': 'venceu ontem' if tipo_escolhido == 'venceu_ontem' else 'hoje' if tipo_escolhido == 'vence_hoje' else 'amanhã',
    }

    payload = montar_email_sendgrid(email_destino, nome, dados, tipo_escolhido)
    sucesso, status_code, message_id, error_message = enviar_email_sendgrid(payload)
    log_disparo_supabase({
        "sistema": "credilly",
        "periodo": tipo_escolhido,
        "cliente_airtable_id": cliente.get('id'),
        "cliente_sistema_id": cliente_id,
        "nome": nome,
        "email": email_destino,
        "valor_parcela": float(parcela.get("valor", 0) or 0),
        "data_vencimento": parcela.get('data_vencimento'),
        "link_pagamento": parcela.get("pdf_url", ""),
        "status": "enviado" if sucesso else "erro",
        "sendgrid_status": status_code,
        "sendgrid_message_id": message_id,
        "error_message": error_message,
        "bcc_aplicado": bool(BCC_ARQUIVO_EMAIL and BCC_SAMPLE_PERCENT and BCC_SAMPLE_PERCENT > 0),
        "bcc_email": BCC_ARQUIVO_EMAIL or None,
        "bcc_sample_percent": BCC_SAMPLE_PERCENT if (BCC_SAMPLE_PERCENT and BCC_SAMPLE_PERCENT > 0) else None,
        "request_payload": {
            "tipo": tipo_escolhido,
            "assunto_ou_template": payload.get('template_id') or payload.get('personalizations', [{}])[0].get('subject'),
        },
    })
    if sucesso:
        logging.info(f"[TESTE-REAIS] Enviado com sucesso para {email_destino} (tipo={tipo_escolhido}).")
    else:
        logging.error(f"[TESTE-REAIS] Falha ao enviar para {email_destino}: {error_message}")

def processar_envio_email():
    inicio = time.time()
    logging.info("\n" + "="*60)
    logging.info("📧 SISTEMA DE E-MAILS - MÚLTIPLOS PERÍODOS")
    logging.info("="*60)
    logging.info(f"📅 Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    logging.info(f"🔧 Modo: {'TESTE' if MODO_TESTE else 'PRODUÇÃO'}")
    logging.info(f"📊 Sistemas: {'Credilly' if PROCESSAR_CREDILLY else ''} {'Turing' if PROCESSAR_TURING else ''}")
    logging.info("="*60 + "\n")
    clientes_dict = buscar_todos_clientes_airtable()
    if not clientes_dict:
        logging.error("❌ Nenhum cliente encontrado no Airtable")
        return
    todas_parcelas = {"venceu_ontem": [], "vence_hoje": [], "vence_amanha": []}
    if PROCESSAR_CREDILLY:
        parcelas_credilly = buscar_parcelas_por_periodo(clientes_dict, 'credilly')
        for periodo, parcelas in parcelas_credilly.items():
            todas_parcelas[periodo].extend(parcelas)
    if PROCESSAR_TURING:
        parcelas_turing = buscar_parcelas_por_periodo(clientes_dict, 'turing')
        for periodo, parcelas in parcelas_turing.items():
            todas_parcelas[periodo].extend(parcelas)
    stats_geral = {"venceu_ontem": processar_parcelas_periodo(todas_parcelas["venceu_ontem"], "venceu_ontem", LIMITE_VENCIDAS), "vence_hoje": processar_parcelas_periodo(todas_parcelas["vence_hoje"], "vence_hoje", LIMITE_HOJE), "vence_amanha": processar_parcelas_periodo(todas_parcelas["vence_amanha"], "vence_amanha", LIMITE_AMANHA)}
    tempo_total = time.time() - inicio
    logging.info("\n" + "="*60)
    logging.info("📊 RELATÓRIO FINAL")
    logging.info("="*60)
    logging.info(f"⏱️ Tempo total: {tempo_total:.2f} segundos")
    total_enviados = 0
    total_processados = 0
    for periodo, stats in stats_geral.items():
        if stats["total"] > 0:
            logging.info(f"\n📅 {periodo.upper().replace('_', ' ')}:")
            logging.info(f"   Total: {stats['total']}")
            logging.info(f"   ✅ Enviados: {stats['enviados']}")
            logging.info(f"   ⏭️ Já enviados hoje: {stats['ja_enviados']}")
            logging.info(f"   📵 Sem e-mail: {stats['sem_email']}")
            logging.info(f"   ❌ Erros: {stats['erros']}")
            total_enviados += stats['enviados']
            total_processados += stats['total']
    logging.info(f"\n📊 TOTAIS:")
    logging.info(f"   Parcelas processadas: {total_processados}")
    logging.info(f"   E-mails enviados: {total_enviados}")
    logging.info("="*60)
    if MODO_TESTE:
        logging.info("\n⚠️ ATENÇÃO: Executado em modo TESTE - nenhum e-mail foi enviado!")
    else:
        send_notification(NOTIFICATION_FINALIZADO_URL)

def lambda_handler(event, context):
    logging.info("Script iniciado em Lambda")
    # Disparo único com dados reais (Airtable + Tenex)
    if os.environ.get('TESTE_DADOS_REAIS', 'false').lower() == 'true':
        email_teste = os.environ.get('EMAIL_TESTE_DESTINO')
        periodo_pref = os.environ.get('PERIODO_TESTE')
        if not email_teste:
            logging.error("TESTE_DADOS_REAIS=true requer EMAIL_TESTE_DESTINO definido.")
            return {'statusCode': 400, 'body': 'EMAIL_TESTE_DESTINO ausente'}
        enviar_teste_com_dados_reais(email_teste, periodo_pref)
        return {'statusCode': 200, 'body': 'Envio único (dados reais) processado'}
    # Disparo único via variáveis de ambiente (não passa por Airtable/Tenex)
    if os.environ.get('TESTE_TEMPLATE_UNICO', 'false').lower() == 'true':
        email_teste = os.environ.get('EMAIL_TESTE_DESTINO')
        tipo_teste = os.environ.get('TIPO_TESTE', 'vence_hoje')
        if not email_teste:
            logging.error("TESTE_TEMPLATE_UNICO=true requer EMAIL_TESTE_DESTINO definido.")
            return {'statusCode': 400, 'body': 'EMAIL_TESTE_DESTINO ausente'}
        if tipo_teste not in ("venceu_ontem", "vence_hoje", "vence_amanha"):
            logging.error("TIPO_TESTE inválido. Use: venceu_ontem | vence_hoje | vence_amanha")
            return {'statusCode': 400, 'body': 'TIPO_TESTE inválido'}
        enviar_teste_template_unico(email_teste, tipo_teste)
        return {'statusCode': 200, 'body': 'Envio único processado'}
    if not verificar_horario_permitido() and not MODO_TESTE:
        logging.warning(f"⚠️ Fora do horário permitido ({HORARIO_INICIO}h-{HORARIO_FIM}h)")
        return {'statusCode': 200, 'body': 'Fora do horário'}
    processar_envio_email()
    return {'statusCode': 200, 'body': 'Processamento concluído'}

if __name__ == "__main__":
    logging.info("Iniciando execução local")
    lambda_handler({}, {})
