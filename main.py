import os
import base64
import json
import smtplib
import fitz  # PyMuPDF
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app, origins="*", allow_headers="*", methods=["GET", "POST", "OPTIONS"])

_client = None

def get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


def enviar_email(destinatario, razao_social, corpo_email):
    remetente = os.environ.get("EMAIL_SENDER")
    senha = os.environ.get("EMAIL_PASSWORD")

    if not remetente or not senha:
        print("EMAIL_SENDER ou EMAIL_PASSWORD não configurados — e-mail não enviado.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Resultado da Análise SST — {razao_social}"
        msg["From"] = remetente
        msg["To"] = destinatario

        # Corpo em texto simples
        parte_texto = MIMEText(corpo_email, "plain", "utf-8")
        msg.attach(parte_texto)

        # Corpo em HTML (formata quebras de linha)
        corpo_html = "<html><body><pre style='font-family:Arial,sans-serif;font-size:14px;white-space:pre-wrap'>" + corpo_email.replace("<", "&lt;").replace(">", "&gt;") + "</pre></body></html>"
        parte_html = MIMEText(corpo_html, "html", "utf-8")
        msg.attach(parte_html)

        with smtplib.SMTP("smtp-mail.outlook.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(remetente, senha)
            server.sendmail(remetente, destinatario, msg.as_string())

        print(f"E-mail enviado com sucesso para {destinatario}")
        return True

    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return False


def extrair_conteudo_pdf(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    texto_total = ""
    imagens = []

    for page in doc:
        texto = page.get_text()
        texto_total += texto

    if len(texto_total.strip()) < 100:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("jpeg")
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            imagens.append(b64)

    doc.close()
    return texto_total.strip(), imagens


def montar_prompt(dados_empresa, colaboradores, documentos_texto, documentos_imagens):
    prompt = f"""Você atuará como Auditor Técnico de Segurança do Trabalho, com base na legislação brasileira (NR-01, NR-07, NR-06) e boas práticas de Sistema de Gestão (ex.: ISO 45001).

REGRAS OBRIGATÓRIAS:
- Basear-se EXCLUSIVAMENTE no documento enviado
- NÃO presumir informações ausentes
- Análise crítica (não apenas descritiva)
- Para cada etapa: Status (✅ APROVADO | ❌ REPROVADO), Evidência (o que foi encontrado), Análise Técnica (avaliação crítica)

DADOS DA EMPRESA:
- Razão Social: {dados_empresa.get('razaoSocial', 'Não informado')}
- CNPJ: {dados_empresa.get('cnpj', 'Não informado')}
- Responsável: {dados_empresa.get('responsavel', 'Não informado')}
- E-mail: {dados_empresa.get('email', 'Não informado')}

DOCUMENTOS RECEBIDOS PARA ANÁLISE:
"""

    if "pgr" in documentos_texto and documentos_texto["pgr"]:
        prompt += f"\n--- CONTEÚDO DO PGR ---\n{documentos_texto['pgr'][:15000]}\n"
    elif "pgr" in documentos_imagens:
        prompt += "\n[PGR enviado como documento escaneado]\n"

    if "pcmso" in documentos_texto and documentos_texto["pcmso"]:
        prompt += f"\n--- CONTEÚDO DO PCMSO ---\n{documentos_texto['pcmso'][:15000]}\n"
    elif "pcmso" in documentos_imagens:
        prompt += "\n[PCMSO enviado como documento escaneado]\n"

    if colaboradores:
        prompt += "\n--- COLABORADORES E ASOs ---\n"
        for i, colab in enumerate(colaboradores):
            nome = colab.get("nome", f"Colaborador {i+1}")
            cargo = colab.get("cargo", "Não informado")
            prompt += f"\nColaborador {i+1}: {nome} | Cargo: {cargo}\n"
            if f"aso_{i}" in documentos_texto and documentos_texto[f"aso_{i}"]:
                prompt += f"ASO: {documentos_texto[f'aso_{i}'][:8000]}\n"

    prompt += """
# POPAF — Prompt Operacional Padronizado para Auditoria Fiscalizatória

## PAPEL
Auditor Técnico de Segurança do Trabalho especializado em auditoria documental, com base em NR-01, NR-06, NR-07 e ISO 45001.
Atuação: técnica, objetiva, crítica, fiel ao documento, SEM inferências ou suposições.

## REGRAS OBRIGATÓRIAS
1. Basear-se EXCLUSIVAMENTE no documento enviado. PROIBIDO presumir, completar ou inferir dados ausentes.
2. Análise crítica obrigatória: avaliar coerência, consistência, conformidade normativa e divergências.
3. Para cada etapa responder: Status (✅ APROVADO | ❌ REPROVADO), Evidência (o que foi encontrado), Análise Técnica (avaliação crítica).
4. APROVADO: todas as verificações conformes. REPROVADO: qualquer não conformidade.

## MATRIZ DE AUDITORIA

PGR (se enviado):
- Etapa 1: Razão Social e CNPJ
- Etapa 2: Inventário de Perigos e Riscos
- Etapa 3: Plano de Ação
- Etapa 4: Responsável Técnico (nome + assinatura + CREA/MTE + tipo assinatura)
- Etapa 5: Vigência definida (máx 2 anos)

PCMSO (se enviado):
- Etapa 6: Razão Social e CNPJ
- Etapa 7: Médico Responsável (nome + CRM + assinatura digital/manual)
- Etapa 8: Vigência 12 meses (data início e fim explícitas)
- Etapa 9: Compatibilidade funções com PGR
- Etapa 10: Riscos idênticos ao PGR
- Etapa 11: Exames ocupacionais (admissional, periódico, retorno, mudança de função + periodicidade)

ASO por colaborador (se enviado):
- Etapa 12: Nome completo + CPF do trabalhador
- Etapa 13: Razão Social + CNPJ da empresa
- Etapa 14: Função presente no PCMSO
- Etapa 15: Setor compatível com PCMSO
- Etapa 16: Tipo de exame (admissional/periódico/retorno/mudança de função/demissional)
- Etapa 17: Riscos idênticos ao PCMSO
- Etapa 18: Data do exame (DD/MM/AAAA)
- Etapa 19: Coerência com planejamento do PCMSO
- Etapa 20: Resultado (APTO ou INAPTO)
- Etapa 21: Médico Responsável (nome + CRM)
- Etapa 22: Assinatura do trabalhador (GOV, digital ou manual)
- Etapa 23: Assinatura do médico + CRM associado

## FORMATO DE ENTREGA
Analise todos os documentos enviados e retorne APENAS JSON válido (sem markdown, sem ```json):
{
  "status_geral": "APROVADO|REPROVADO|PARCIALMENTE APROVADO",
  "analises": [
    {
      "documento": "PGR|PCMSO|ASO - [Nome do Colaborador]",
      "status": "APROVADO|REPROVADO",
      "etapas": [
        {
          "numero": 1,
          "nome": "Dados da empresa",
          "status": "✅ APROVADO",
          "evidencia": "descrição objetiva do encontrado",
          "analise_tecnica": "avaliação crítica de conformidade"
        }
      ]
    }
  ],
  "pendencias": ["lista de não conformidades"],
  "recomendacoes": ["ações corretivas sugeridas"],
  "email_resposta": "e-mail formal em português para enviar ao fornecedor com o resultado"
}
"""
    return prompt


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Backend SST funcionando com OpenAI"})


@app.route("/analisar", methods=["POST", "OPTIONS"])
def analisar():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200

    try:
        dados_empresa = {
            "razaoSocial": request.form.get("razaoSocial", ""),
            "cnpj": request.form.get("cnpj", ""),
            "responsavel": request.form.get("responsavel", ""),
            "email": request.form.get("email", ""),
        }

        colaboradores_json = request.form.get("colaboradores", "[]")
        try:
            colaboradores = json.loads(colaboradores_json)
        except Exception:
            colaboradores = []

        documentos_texto = {}
        documentos_imagens = {}
        content_parts = []

        # Processar PGR
        if "pgr" in request.files:
            arquivo = request.files["pgr"]
            bytes_pdf = arquivo.read()
            texto, imagens = extrair_conteudo_pdf(bytes_pdf)
            if texto:
                documentos_texto["pgr"] = texto
            elif imagens:
                documentos_imagens["pgr"] = imagens
                for img_b64 in imagens[:5]:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                    })

        # Processar PCMSO
        if "pcmso" in request.files:
            arquivo = request.files["pcmso"]
            bytes_pdf = arquivo.read()
            texto, imagens = extrair_conteudo_pdf(bytes_pdf)
            if texto:
                documentos_texto["pcmso"] = texto
            elif imagens:
                documentos_imagens["pcmso"] = imagens
                for img_b64 in imagens[:5]:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                    })

        # Processar ASOs
        for i, colab in enumerate(colaboradores):
            chave = f"aso_{i}"
            if chave in request.files:
                arquivo = request.files[chave]
                bytes_pdf = arquivo.read()
                texto, imagens = extrair_conteudo_pdf(bytes_pdf)
                if texto:
                    documentos_texto[chave] = texto
                elif imagens:
                    documentos_imagens[chave] = imagens
                    for img_b64 in imagens[:3]:
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                        })

        prompt_texto = montar_prompt(dados_empresa, colaboradores, documentos_texto, documentos_imagens)
        content_parts.append({"type": "text", "text": prompt_texto})

        client = get_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": content_parts}],
            max_tokens=8000
        )

        texto_resposta = response.choices[0].message.content

        try:
            # Remover markdown se presente
            texto_limpo = texto_resposta.strip()
            if "```json" in texto_limpo:
                texto_limpo = texto_limpo.split("```json")[1].split("```")[0].strip()
            elif "```" in texto_limpo:
                texto_limpo = texto_limpo.split("```")[1].split("```")[0].strip()

            inicio = texto_limpo.find("{")
            fim = texto_limpo.rfind("}") + 1
            if inicio >= 0 and fim > inicio:
                resultado = json.loads(texto_limpo[inicio:fim])
            else:
                resultado = {"status_geral": "ERRO", "resposta_bruta": texto_resposta}
        except Exception:
            resultado = {"status_geral": "ERRO", "resposta_bruta": texto_resposta}

        # Enviar e-mail ao fornecedor se houver email_resposta
        email_destino = dados_empresa.get("email", "")
        email_corpo = resultado.get("email_resposta", "")
        razao = dados_empresa.get("razaoSocial", "Fornecedor")

        if email_destino and email_corpo:
            enviado = enviar_email(email_destino, razao, email_corpo)
            resultado["email_enviado"] = enviado
        else:
            resultado["email_enviado"] = False

        return jsonify(resultado)

    except Exception as e:
        return jsonify({"error": str(e), "status_geral": "ERRO"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
