from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import base64
import os
import json
import pymupdf

app = Flask(__name__)
CORS(app, origins="*", allow_headers="*", methods=["GET", "POST", "OPTIONS"])

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

TIN_PROMPT = """Voce atuara como Auditor Tecnico de Seguranca do Trabalho, com base na legislacao brasileira (NR-01, NR-07, NR-06) e boas praticas de Sistema de Gestao (ISO 45001).

REGRAS OBRIGATORIAS:
- Basear-se EXCLUSIVAMENTE no conteudo real dos documentos fornecidos.
- NAO presumir informacoes. NAO completar dados ausentes.
- Realizar analise critica: avaliar coerencia, consistencia e conformidade.
- Para cada etapa: Status (APROVADO/REPROVADO/AUSENTE), Evidencia (trecho real do documento) e Analise Tecnica objetiva.
- Analisar SOMENTE os documentos enviados. Documentos nao enviados = etapas AUSENTE.

CRITERIOS DE ANALISE - PGR (etapas 1 a 5):
Etapa 1 - Dados da empresa: verificar Razao Social e CNPJ presentes.
Etapa 2 - Inventario de Riscos: buscar Inventario de Perigos e Riscos ou equivalente.
Etapa 3 - Plano de Acao: verificar existencia de plano de acao.
Etapa 4 - Responsavel Tecnico: nome + assinatura + registro profissional (CREA/MTE) + tipo de assinatura.
Etapa 5 - Vigencia: deve conter vigencia definida de ate 2 anos.

CRITERIOS DE ANALISE - PCMSO (etapas 6 a 11):
Etapa 6 - Dados da empresa: Razao Social e CNPJ.
Etapa 7 - Medico Responsavel: nome + CRM + assinatura.
Etapa 8 - Vigencia: 12 meses com data explicita de inicio e fim.
Etapa 9 - Compatibilidade com PGR: todas as funcoes do PGR devem estar no PCMSO.
Etapa 10 - Compatibilidade de Riscos: riscos identicos ao PGR.
Etapa 11 - Exames Ocupacionais: tipos e periodicidade definida.

CRITERIOS DE ANALISE - ASO (etapas 12 a 23 por colaborador):
Etapa 12 - Dados do trabalhador: nome completo + CPF.
Etapa 13 - Dados da empresa: Razao Social + CNPJ.
Etapa 14 - Compatibilidade com PCMSO: funcao presente no PCMSO.
Etapa 15 - Setor: deve coincidir com PCMSO.
Etapa 16 - Tipo de exame: admissional/periodico/retorno/mudanca de funcao.
Etapa 17 - Riscos Ocupacionais: devem ser identicos ao PCMSO.
Etapa 18 - Data do exame: formato valido (DD/MM/AAAA).
Etapa 19 - Coerencia com PCMSO: tipo de exame compativel com planejamento.
Etapa 20 - Resultado: APTO ou INAPTO.
Etapa 21 - Medico Responsavel: nome + CRM.
Etapa 22 - Assinatura do trabalhador: presente.
Etapa 23 - Assinatura do medico: presente + CRM."""


def extract_pdf_content(pdf_bytes, filename):
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"

        if len(full_text.replace(" ", "").replace("\n", "")) > 200:
            doc.close()
            return [{"type": "text", "text": f"=== DOCUMENTO: {filename} ===\n{full_text[:15000]}"}]

        content_blocks = [{"type": "text", "text": f"=== DOCUMENTO ESCANEADO: {filename} ==="}]
        for i, page in enumerate(doc):
            mat = pymupdf.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("jpeg", jpg_quality=75)
            img_b64 = base64.b64encode(img_bytes).decode()
            content_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
            })
            content_blocks.append({"type": "text", "text": f"[Pagina {i+1} de {len(doc)}]"})

        doc.close()
        return content_blocks

    except Exception as e:
        return [{"type": "text", "text": f"=== DOCUMENTO: {filename} - Erro: {str(e)} ==="}]


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/analisar", methods=["POST", "OPTIONS"])
def analisar():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        razao       = request.form.get("razao", "")
        cnpj        = request.form.get("cnpj", "")
        email       = request.form.get("email", "")
        responsavel = request.form.get("responsavel", "")
        atividade   = request.form.get("atividade", "")
        colabs      = json.loads(request.form.get("colabs", "[]"))

        has_pgr = "pgr" in request.files
        has_pcmso = "pcmso" in request.files

        colab_list = "\n".join([
            f"  {i+1}. Nome: {c.get('name','Sem nome')} | Cargo: {c.get('cargo','nao informado')}"
            for i, c in enumerate(colabs)
        ]) or "  Nenhum"

        content = []
        content.append({"type": "text", "text": f"""{TIN_PROMPT}

DADOS DO FORNECEDOR:
Empresa: {razao} | CNPJ: {cnpj or 'Nao informado'} | Responsavel: {responsavel or 'Nao informado'} | Atividade: {atividade or 'Nao informada'} | E-mail: {email}
Colaboradores ({len(colabs)}): {colab_list}

A seguir estao os documentos para analise:"""})

        for doc_key in ["pgr", "pcmso"]:
            if doc_key in request.files:
                f = request.files[doc_key]
                content.extend(extract_pdf_content(f.read(), f.filename))

        for i, colab in enumerate(colabs):
            for doc_type in ["aso", "os", "epi", "trein"]:
                field_name = f"colab_{i}_{doc_type}"
                if field_name in request.files:
                    f = request.files[field_name]
                    label = {"aso":"ASO","os":"Ordem de Servico","epi":"Ficha de EPI","trein":"Treinamento"}[doc_type]
                    content.extend(extract_pdf_content(f.read(), f"{colab.get('name','Colaborador')} - {label}"))

        # Build JSON schema based on what was sent
        pgr_schema = '''"pgr":{"status":"ok|pendente|reprovado|ausente","validade":"DD/MM/AAAA ou N/A","obs":"string","etapas":[{"num":1,"nome":"Dados da empresa","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"trecho real","analise":"avaliacao"},{"num":2,"nome":"Inventario de Riscos","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":3,"nome":"Plano de Acao","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":4,"nome":"Responsavel Tecnico","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":5,"nome":"Vigencia","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"}]}''' if has_pgr else '''"pgr":{"status":"ausente","validade":"N/A","obs":"Documento nao enviado","etapas":[]}'''

        pcmso_schema = '''"pcmso":{"status":"ok|pendente|reprovado|ausente","validade":"DD/MM/AAAA ou N/A","obs":"string","etapas":[{"num":6,"nome":"Dados da empresa","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":7,"nome":"Medico Responsavel","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":8,"nome":"Vigencia","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":9,"nome":"Compatibilidade com PGR","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":10,"nome":"Compatibilidade de Riscos","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":11,"nome":"Exames Ocupacionais","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"}]}''' if has_pcmso else '''"pcmso":{"status":"ausente","validade":"N/A","obs":"Documento nao enviado","etapas":[]}'''

        colab_schema = '''"colaboradores":[{"nome":"string","cargo":"string","status":"ok|pendente|reprovado","aso":{"status":"ok|pendente|ausente","validade":"DD/MM/AAAA ou N/A"},"os":{"status":"ok|pendente|ausente"},"epi":{"status":"ok|pendente|ausente"},"trein":{"status":"ok|pendente|ausente"},"etapas_aso":[{"num":12,"nome":"Dados do trabalhador","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":13,"nome":"Dados da empresa","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":14,"nome":"Compatibilidade com PCMSO","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":15,"nome":"Setor","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":16,"nome":"Tipo de exame","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":17,"nome":"Riscos Ocupacionais","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":18,"nome":"Data do exame","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":19,"nome":"Coerencia com PCMSO","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":20,"nome":"Resultado","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":21,"nome":"Medico Responsavel","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":22,"nome":"Assinatura do trabalhador","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"},{"num":23,"nome":"Assinatura do medico","status":"APROVADO|REPROVADO|AUSENTE","evidencia":"string","analise":"string"}]}]''' if colabs else '"colaboradores":[]'

        schema = f'{{"status_geral":"aprovado|pendente|reprovado","empresa":{{{pgr_schema},{pcmso_schema}}},{colab_schema},"pendencias":["string"],"recomendacoes":["string"],"email_resposta":"e-mail formal completo em portugues"}}'

        content.append({"type": "text", "text": f"\nRetorne SOMENTE este JSON valido, sem texto adicional:\n{schema}"})

        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": content}]
        )

        text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        result = json.loads(text)
        return jsonify({"success": True, "result": result})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
