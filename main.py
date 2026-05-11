import os
import base64
import json
import fitz  # PyMuPDF
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic

app = Flask(__name__)
CORS(app, origins="*", allow_headers="*", methods=["GET", "POST", "OPTIONS"])

_client = None

def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


def extrair_conteudo_pdf(file_bytes):
    """Extrai texto ou imagens de um PDF."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    texto_total = ""
    imagens = []

    for page in doc:
        texto = page.get_text()
        texto_total += texto

    # Se o PDF tem pouco texto, é escaneado — extrair como imagens
    if len(texto_total.strip()) < 100:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("jpeg")
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            imagens.append(b64)

    doc.close()
    return texto_total.strip(), imagens


def montar_prompt(dados_empresa, colaboradores, documentos_texto, documentos_imagens):
    """Monta o prompt completo com o TIN integrado."""

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
    elif "pgr" in documentos_imagens and documentos_imagens["pgr"]:
        prompt += "\n[PGR enviado como documento escaneado — analise as imagens anexadas]\n"

    if "pcmso" in documentos_texto and documentos_texto["pcmso"]:
        prompt += f"\n--- CONTEÚDO DO PCMSO ---\n{documentos_texto['pcmso'][:15000]}\n"
    elif "pcmso" in documentos_imagens and documentos_imagens["pcmso"]:
        prompt += "\n[PCMSO enviado como documento escaneado — analise as imagens anexadas]\n"

    if colaboradores:
        prompt += "\n--- COLABORADORES E ASOs ---\n"
        for i, colab in enumerate(colaboradores):
            nome = colab.get("nome", f"Colaborador {i+1}")
            cargo = colab.get("cargo", "Não informado")
            prompt += f"\nColaborador {i+1}: {nome} | Cargo: {cargo}\n"
            if f"aso_{i}" in documentos_texto and documentos_texto[f"aso_{i}"]:
                prompt += f"ASO: {documentos_texto[f'aso_{i}'][:8000]}\n"

    prompt += """
REALIZE A ANÁLISE COMPLETA SEGUINDO AS ETAPAS:

📊 1ª ANÁLISE – PGR (se enviado):
- Etapa 1: Dados da empresa (Razão Social e CNPJ)
- Etapa 2: Inventário de Riscos
- Etapa 3: Plano de Ação
- Etapa 4: Responsável Técnico (Nome + assinatura + CREA/MTE)
- Etapa 5: Vigência (máximo 2 anos)

📊 2ª ANÁLISE – PCMSO (se enviado):
- Etapa 6: Dados da empresa
- Etapa 7: Médico Responsável (Nome + CRM + assinatura)
- Etapa 8: Vigência (12 meses, com datas explícitas)
- Etapa 9: Compatibilidade com PGR (funções)
- Etapa 10: Compatibilidade de Riscos com PGR
- Etapa 11: Exames Ocupacionais (tipos e periodicidade)

📊 3ª ANÁLISE – ASO por colaborador (se enviado):
- Etapa 12: Dados do trabalhador (Nome + CPF)
- Etapa 13: Dados da empresa (Razão Social + CNPJ)
- Etapa 14: Compatibilidade com PCMSO (função)
- Etapa 15: Setor (coincide com PCMSO)
- Etapa 16: Tipo de exame
- Etapa 17: Riscos Ocupacionais (idênticos ao PCMSO)
- Etapa 18: Data do exame (DD/MM/AAAA)
- Etapa 19: Coerência com PCMSO
- Etapa 20: Resultado (APTO ou INAPTO)
- Etapa 21: Médico Responsável (Nome + CRM)
- Etapa 22: Assinatura do trabalhador
- Etapa 23: Assinatura do médico + CRM

CONCLUSÃO: Apresente uma tabela com o resumo de todas as etapas analisadas (aprovadas e reprovadas), identificando o colaborador quando aplicável.

Ao final, gere um e-mail formal em português com o resultado para enviar ao fornecedor.

Retorne a resposta em JSON com esta estrutura:
{
  "status_geral": "APROVADO" | "REPROVADO" | "PARCIALMENTE APROVADO",
  "analises": [
    {
      "documento": "PGR" | "PCMSO" | "ASO - [Nome do colaborador]",
      "status": "APROVADO" | "REPROVADO",
      "etapas": [
        {
          "numero": 1,
          "nome": "Dados da empresa",
          "status": "✅ APROVADO" | "❌ REPROVADO",
          "evidencia": "...",
          "analise_tecnica": "..."
        }
      ]
    }
  ],
  "pendencias": ["..."],
  "recomendacoes": ["..."],
  "email_resposta": "..."
}
"""
    return prompt


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Backend SST funcionando"})


@app.route("/analisar", methods=["POST", "OPTIONS"])
def analisar():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200

    try:
        # Dados da empresa
        dados_empresa = {
            "razaoSocial": request.form.get("razaoSocial", ""),
            "cnpj": request.form.get("cnpj", ""),
            "responsavel": request.form.get("responsavel", ""),
            "email": request.form.get("email", ""),
        }

        # Colaboradores
        colaboradores_json = request.form.get("colaboradores", "[]")
        try:
            colaboradores = json.loads(colaboradores_json)
        except Exception:
            colaboradores = []

        documentos_texto = {}
        documentos_imagens = {}
        content_blocks = []

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
                    content_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
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
                    content_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
                    })

        # Processar ASOs dos colaboradores
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
                        content_blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
                        })

        # Montar prompt
        prompt_texto = montar_prompt(
            dados_empresa, colaboradores, documentos_texto, documentos_imagens
        )

        # Montar mensagem para Claude
        content_blocks.append({"type": "text", "text": prompt_texto})

        # Chamar API Claude
        client = get_client()
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": content_blocks}]
        )

        texto_resposta = response.content[0].text

        # Tentar extrair JSON da resposta
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
