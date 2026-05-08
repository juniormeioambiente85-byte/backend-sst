from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import base64
import io
import os
import pymupdf  # PyMuPDF

app = Flask(__name__)
CORS(app, origins="*", allow_headers="*", methods=["GET", "POST", "OPTIONS"])

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

TIN_PROMPT = """Você atuará como Auditor Técnico de Segurança do Trabalho, com base na legislação brasileira (NR-01, NR-07, NR-06) e boas práticas de Sistema de Gestão (ISO 45001).

REGRAS OBRIGATÓRIAS:
- Basear-se EXCLUSIVAMENTE no conteúdo real dos documentos fornecidos.
- NÃO presumir informações. NÃO completar dados ausentes.
- Realizar análise crítica: avaliar coerência, consistência e conformidade.
- Para cada etapa: Status (APROVADO/REPROVADO/AUSENTE), Evidência (trecho real do documento) e Análise Técnica objetiva.
- Analisar SOMENTE os documentos enviados. Documentos não enviados = etapas AUSENTE.

CRITÉRIOS DE ANÁLISE — PGR (etapas 1 a 5):
Etapa 1 – Dados da empresa: verificar Razão Social e CNPJ presentes.
Etapa 2 – Inventário de Riscos: buscar "Inventário de Perigos e Riscos" ou equivalente.
Etapa 3 – Plano de Ação: verificar existência de plano de ação.
Etapa 4 – Responsável Técnico: nome + assinatura + registro profissional (CREA/MTE) + tipo de assinatura.
Etapa 5 – Vigência: deve conter vigência definida de até 2 anos.

CRITÉRIOS DE ANÁLISE — PCMSO (etapas 6 a 11):
Etapa 6 – Dados da empresa: Razão Social e CNPJ.
Etapa 7 – Médico Responsável: nome + CRM + assinatura.
Etapa 8 – Vigência: 12 meses com data explícita de início e fim.
Etapa 9 – Compatibilidade com PGR: todas as funções do PGR devem estar no PCMSO.
Etapa 10 – Compatibilidade de Riscos: riscos idênticos ao PGR.
Etapa 11 – Exames Ocupacionais: tipos e periodicidade definida.

CRITÉRIOS DE ANÁLISE — ASO (etapas 12 a 23 por colaborador):
Etapa 12 – Dados do trabalhador: nome completo + CPF.
Etapa 13 – Dados da empresa: Razão Social + CNPJ.
Etapa 14 – Compatibilidade com PCMSO: função presente no PCMSO.
Etapa 15 – Setor: deve coincidir com PCMSO.
Etapa 16 – Tipo de exame: admissional/periódico/retorno/mudança de função.
Etapa 17 – Riscos Ocupacionais: devem ser idênticos ao PCMSO.
Etapa 18 – Data do exame: formato válido (DD/MM/AAAA).
Etapa 19 – Coerência com PCMSO: tipo de exame compatível com planejamento.
Etapa 20 – Resultado: APTO ou INAPTO.
Etapa 21 – Médico Responsável: nome + CRM.
Etapa 22 – Assinatura do trabalhador: presente.
Etapa 23 – Assinatura do médico: presente + CRM."""

def extract_pdf_content(pdf_bytes, filename):
    """Extract text from digital PDF or images from scanned PDF."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        
        # Try text extraction first
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"
        
        # If meaningful text found, use it
        if len(full_text.replace(" ", "").replace("\n", "")) > 200:
            doc.close()
            return [{"type": "text", "text": f"=== DOCUMENTO: {filename} ===\n{full_text[:15000]}"}]
        
        # Otherwise it's scanned — convert pages to images
        content_blocks = [{"type": "text", "text": f"=== DOCUMENTO ESCANEADO: {filename} ==="}]
        for i, page in enumerate(doc):
            mat = pymupdf.Matrix(1.5, 1.5)  # scale for quality
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("jpeg", jpg_quality=75)
            img_b64 = base64.b64encode(img_bytes).decode()
            content_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
            })
            content_blocks.append({"type": "text", "text": f"[Página {i+1} de {len(doc)}]"})
        
        doc.close()
        return content_blocks
        
    except Exception as e:
        return [{"type": "text", "text": f"=== DOCUMENTO: {filename} — Erro ao processar: {str(e)} ==="}]


def build_json_schema(has_pgr, has_pcmso, colabs):
    pgr_schema = ""
    if has_pgr:
        pgr_schema = '''"pgr": {
      "status": "ok|pendente|reprovado|ausente", "validade": "DD/MM/AAAA ou N/A", "obs": "string",
      "etapas": [
        {"num":1,"nome":"Dados da empresa","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"trecho real","analise":"avaliação"},
        {"num":2,"nome":"Inventário de Riscos","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":3,"nome":"Plano de Ação","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":4,"nome":"Responsável Técnico","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":5,"nome":"Vigência","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"}
      ]}'''
    
    pcmso_schema = ""
    if has_pcmso:
        pcmso_schema = '''"pcmso": {
      "status": "ok|pendente|reprovado|ausente", "validade": "DD/MM/AAAA ou N/A", "obs": "string",
      "etapas": [
        {"num":6,"nome":"Dados da empresa","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":7,"nome":"Médico Responsável","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":8,"nome":"Vigência","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":9,"nome":"Compatibilidade com PGR","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":10,"nome":"Compatibilidade de Riscos","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":11,"nome":"Exames Ocupacionais","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"}
      ]}'''

    colab_schema = ""
    if colabs:
        colab_schema = '''"colaboradores": [{"nome":"string","cargo":"string","status":"ok|pendente|reprovado",
      "aso":{"status":"ok|pendente|ausente","validade":"DD/MM/AAAA ou N/A"},
      "os":{"status":"ok|pendente|ausente"},"epi":{"status":"ok|pendente|ausente"},"trein":{"status":"ok|pendente|ausente"},
      "etapas_aso":[
        {"num":12,"nome":"Dados do trabalhador","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":13,"nome":"Dados da empresa","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":14,"nome":"Compatibilidade com PCMSO","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":15,"nome":"Setor","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":16,"nome":"Tipo de exame","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":17,"nome":"Riscos Ocupacionais","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":18,"nome":"Data do exame","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":19,"nome":"Coerência com PCMSO","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":20,"nome":"Resultado","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":21,"nome":"Médico Responsável","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":22,"nome":"Assinatura do trabalhador","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},
        {"num":23,"nome":"Assinatura do médico","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"}
      ]}]'''

    empresa_parts = ", ".join(filter(None, [pgr_schema, pcmso_schema]))
    
    return f'''{{
  "status_geral": "aprovado|pendente|reprovado",
  "empresa": {{{empresa_parts}}},
  {colab_schema if colab_schema else '"colaboradores": []'},
  "pendencias": ["string"],
  "recomendacoes": ["string"],
  "email_resposta": "e-mail completo formal em português com resultado detalhado, evidências, pendências e próximos passos"
}}'''


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/analisar", methods=["POST"])
def analisar():
    try:
        razao       = request.form.get("razao", "")
        cnpj        = request.form.get("cnpj", "")
        email       = request.form.get("email", "")
        responsavel = request.form.get("responsavel", "")
        atividade   = request.form.get("atividade", "")
        colabs_json = request.form.get("colabs", "[]")
        
        import json
        colabs = json.loads(colabs_json)

        # Build message content
        content = []

        # Intro text
        colab_list = "\n".join([
            f"  {i+1}. Nome: {c.get('name','Sem nome')} | Cargo: {c.get('cargo','não informado')} | Docs: {', '.join(c.get('docs',[]))}"
            for i, c in enumerate(colabs)
        ]) or "  Nenhum"

        content.append({"type": "text", "text": f"""{TIN_PROMPT}

DADOS DO FORNECEDOR:
Empresa: {razao} | CNPJ: {cnpj or 'Não informado'} | Responsável: {responsavel or 'Não informado'} | Atividade: {atividade or 'Não informada'} | E-mail: {email}

Colaboradores ({len(colabs)}):
{colab_list}

A seguir estão os documentos enviados para análise:"""})

        has_pgr = False
        has_pcmso = False

        # Process company docs
        for doc_key in ["pgr", "pcmso"]:
            if doc_key in request.files:
                f = request.files[doc_key]
                pdf_bytes = f.read()
                blocks = extract_pdf_content(pdf_bytes, f.filename)
                content.extend(blocks)
                if doc_key == "pgr":   has_pgr = True
                if doc_key == "pcmso": has_pcmso = True

        # Process collaborator docs
        for i, colab in enumerate(colabs):
            for doc_type in ["aso", "os", "epi", "trein"]:
                field_name = f"colab_{i}_{doc_type}"
                if field_name in request.files:
                    f = request.files[field_name]
                    pdf_bytes = f.read()
                    label = {"aso":"ASO","os":"Ordem de Serviço","epi":"Ficha de EPI","trein":"Treinamento"}[doc_type]
                    blocks = extract_pdf_content(pdf_bytes, f"{colab.get('name','Colaborador')} — {label}")
                    content.extend(blocks)

        # JSON schema instruction
        json_schema = build_json_schema(has_pgr, has_pcmso, colabs)
        content.append({"type": "text", "text": f"\nRetorne SOMENTE este JSON válido, sem texto adicional:\n{json_schema}"})

        # Call Claude API
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": content}]
        )

        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        
        # Clean and parse JSON
        text_clean = text.strip()
        if text_clean.startswith("```"):
            text_clean = text_clean.split("```")[1]
            if text_clean.startswith("json"):
                text_clean = text_clean[4:]
        text_clean = text_clean.strip()

        result = json.loads(text_clean)
        return jsonify({"success": True, "result": result})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
