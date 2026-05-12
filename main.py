import os
import base64
import json
import fitz  # PyMuPDF
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
ANALISE apenas os documentos enviados. Para cada etapa: status (✅ APROVADO|❌ REPROVADO), evidência encontrada e análise técnica objetiva.

PGR (etapas 1-5): 1-Razão Social/CNPJ 2-Inventário de Riscos 3-Plano de Ação 4-Responsável Técnico+CREA/MTE 5-Vigência(máx 2 anos)
PCMSO (etapas 6-11): 6-Dados empresa 7-Médico+CRM+assinatura 8-Vigência 12 meses 9-Compatibilidade funções c/PGR 10-Riscos idênticos ao PGR 11-Exames e periodicidade
ASO por colaborador (etapas 12-23): 12-Nome+CPF 13-Empresa+CNPJ 14-Função no PCMSO 15-Setor 16-Tipo exame 17-Riscos=PCMSO 18-Data(DD/MM/AAAA) 19-Coerência PCMSO 20-APTO/INAPTO 21-Médico+CRM 22-Assinatura trabalhador 23-Assinatura médico+CRM

Retorne APENAS JSON válido (sem markdown):
{"status_geral":"APROVADO|REPROVADO|PARCIALMENTE APROVADO","analises":[{"documento":"PGR|PCMSO|ASO - [Nome]","status":"APROVADO|REPROVADO","etapas":[{"numero":1,"nome":"Dados da empresa","status":"✅ APROVADO","evidencia":"...","analise_tecnica":"..."}]}],"pendencias":["..."],"recomendacoes":["..."],"email_resposta":"..."}
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
            inicio = texto_resposta.find("{")
            fim = texto_resposta.rfind("}") + 1
            if inicio >= 0 and fim > inicio:
                resultado = json.loads(texto_resposta[inicio:fim])
            else:
                resultado = {"status_geral": "ERRO", "resposta_bruta": texto_resposta}
        except Exception:
            resultado = {"status_geral": "ERRO", "resposta_bruta": texto_resposta}

        return jsonify(resultado)

    except Exception as e:
        return jsonify({"error": str(e), "status_geral": "ERRO"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
