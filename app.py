from flask import Flask, request, redirect, url_for, render_template, send_file, session, flash
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import pandas as pd
import io
import base64
from threading import Timer
from dotenv import load_dotenv
from supabase import create_client
import webbrowser
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from functools import wraps
from openpyxl.styles import Alignment
from werkzeug.security import generate_password_hash, check_password_hash

# --------Load Environment Variables-------
load_dotenv()

# --------Configure Logging---------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------Initialize Scheduler-------------
# Disable scheduler in debug mode to prevent duplicate jobs
scheduler = None
logger.info("Scheduler initialization deferred - will be set up after Flask app creation")

# --------Initialize Supabase-------
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# --------Initialize Flask App-------
app = Flask(__name__)

# ------------Login Credentials-------------
app.secret_key = os.getenv('APP_SECRET_KEY')

# --------Initialize scheduler after Flask app is created--------
if not app.debug and not os.environ.get('WERKZEUG_RUN_MAIN'):
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.start()
    logger.info("Email scheduler started")
    atexit.register(lambda: scheduler.shutdown() if scheduler else None)
else:
    logger.info("Skipping email scheduler in debug mode to prevent duplicates")
    scheduler = None

# --------Email Configuration------------
app.config['SMTP_SERVER'] = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
app.config['SMTP_PORT'] = int(os.getenv('SMTP_PORT', 465))
app.config['SMTP_USERNAME'] = os.getenv('SMTP_USERNAME')
app.config['SMTP_PASSWORD'] = os.getenv('SMTP_PASSWORD')


# ---------Login/Logout Functions--------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- Email Functions ---
def get_email_template_content(nacionalidade, template_type='primeiro'):
    """Get email template content from database or fallback to default templates"""
    try:
        # Always try files first, then check for custom database templates
        template_files = {
            'português': 'email_feedback.html',
            'inglês': 'email_feedback_internacional_ingles.html',
            'alemão': 'email_feedback_internacional_alemao.html',
            'francês': 'email_feedback_internacional_frances.html',
            'dinamarques': 'email_feedback_internacional_dinamarques.html',
            'espanhol': 'email_feedback_internacional_espanhol.html',
            'noruegues': 'email_feedback_internacional_noruegues.html',
            'polaco': 'email_feedback_internacional_polaco.html',
            'sueco': 'email_feedback_internacional_sueco.html',
        }

        template_file = template_files.get(nacionalidade, 'email_feedback.html')
        with app.app_context():
            file_content = render_template(template_file, nome="[NOME]")

        # Check if there's a custom template in database
        try:
            response = supabase.table("email_templates").select("*").eq("nacionalidade", nacionalidade).eq("tipo",
                                                                                                           template_type).execute()

            if response.data and response.data[0]['conteudo'].strip():
                return response.data[0]['conteudo']
            else:
                return file_content
        except Exception as db_error:
            logger.error(f"Database error, using file template: {str(db_error)}")
            return file_content

    except Exception as e:
        logger.error(f"Error getting template content: {str(e)}")
        # Return a simple fallback template
        return f"<p>Olá [NOME],</p><p>Obrigado pela sua experiência de mergulho!</p><p>Atenciosamente,<br>Atlantic Diving Center</p>"


def enviar_email(destinatario, assunto, nome, internacional, template_type='primeiro'):
    try:
        # Get template content (custom or default)
        template_content = get_email_template_content(internacional, template_type)

        # Replace [NOME] placeholder with actual name
        corpo_html = template_content.replace('[NOME]', nome)

        # Create message
        msg = MIMEMultipart("alternative")
        msg['From'] = app.config['SMTP_USERNAME']
        msg['To'] = destinatario
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo_html, "html"))

        with smtplib.SMTP_SSL(app.config['SMTP_SERVER'], app.config['SMTP_PORT']) as server:
            server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
            server.send_message(msg)

        logger.info(
            f"EMAIL SENT: {destinatario} | Subject: {assunto} | Template: {template_type} | Time: {datetime.now()}")
        return True
    except Exception as e:
        logger.error(f"Email failed: {str(e)}")
        return False


def email_feedback(cliente, template_type='primeiro'):
    assunto = {
        'inglês': "Thank you for your diving experience!",
        'francês': "Merci d'avoir plongé avec nous",
        'alemão': "Danke für Ihr Taucherlebnis",
        'dinamarques': "Tak for din dykkeroplevelse!",
        'espanhol': "¡Gracias por tu experiencia de buceo!",
        'noruegues': "Takk for din dykkeopplevelse!",
        'polaco': "Dziękujemy za Twoje doświadczenie nurkowe!",
        'sueco': "Tack för din dykupplevelse!",
    }.get(cliente['nacionalidade'], "Obrigado pela sua experiência de mergulho!")
    return enviar_email(cliente['email'], assunto, cliente['nome'], cliente['nacionalidade'], template_type)

# ----------Request-Based Email Checker-----------
def check_and_send_emails():
    with app.app_context():  # Ensure Flask context
        hoje = datetime.now().date()
        response = supabase.table("clientes").select("*").execute()
        clientes = response.data

        logger.info(f"=== SCHEDULED EMAIL CHECK STARTED ===")
        logger.info(f"Checking emails for {len(clientes)} clients")

        for cliente in clientes:
            data_mergulho = datetime.strptime(cliente['data_mergulho'], '%Y-%m-%d').date()
            dias_passados = (hoje - data_mergulho).days

            if dias_passados >= 1 and not cliente['primeiro_email_enviado']:
                logger.info(f"SENDING: Sending first email to {cliente['email']}")
                email_feedback(cliente, 'primeiro')
                supabase.table("clientes").update(
                    {"primeiro_email_enviado": True}
                ).eq("email", cliente['email']).execute()
                logger.info(f"SENT: First email sent successfully to {cliente['email']}")

            if dias_passados >= 3 and not cliente['segundo_email_enviado']:
                logger.info(f"SCHEDULED: Sending second email to {cliente['email']} (day {dias_passados})")
                if email_feedback(cliente, 'segundo'):
                    supabase.table("clientes").update(
                        {"segundo_email_enviado": True}
                    ).eq("email", cliente['email']).execute()
                    logger.info(f"SCHEDULED: Second email sent successfully to {cliente['email']}")

        logger.info(f"=== SCHEDULED EMAIL CHECK COMPLETED ===")


# ------Email Sending Scheduler-------
# Only add scheduler job if scheduler exists and not in debug mode
if scheduler is not None:
    try:
        # Remove any existing email check jobs to prevent duplicates
        existing_jobs = scheduler.get_jobs()
        for job in existing_jobs:
            if job.id == 'email_check_job':
                scheduler.remove_job('email_check_job')
                logger.info("Removed existing email check job")

        # Add the job
        scheduler.add_job(
            check_and_send_emails,
            'interval',
            minutes=1,
            timezone="Europe/Lisbon",
            id='email_check_job'
        )
        logger.info("Email check job added to scheduler")
    except Exception as e:
        logger.error(f"Error setting up email scheduler: {str(e)}")
        # Only add job if it doesn't already exist
        try:
            scheduler.add_job(
                check_and_send_emails,
                'interval',
                minutes=1,
                timezone="Europe/Lisbon",
                id='email_check_job'
            )
            logger.info("Email check job added to scheduler (fallback)")
        except Exception as fallback_error:
            logger.error(f"Failed to add email check job in fallback: {str(fallback_error)}")
else:
    logger.info("Skipping email scheduler - scheduler not available")


# ---------Time-Checker----------
# Manual email checking for debug mode
@app.route('/check-emails-manual', methods=['POST'])
@login_required
def check_emails_manual():
    """Manual trigger for email checking (useful in debug mode)"""
    if not session.get('is_admin'):
        return 'Unauthorized', 403

    logger.info("Manual email check triggered")
    check_and_send_emails()
    return 'Email check completed', 200


@app.route('/clear-email-templates', methods=['POST'])
@login_required
def clear_email_templates():
    """Clear all email templates from database (for testing)"""
    if not session.get('is_admin'):
        return 'Unauthorized', 403

    try:
        # Delete all email templates
        supabase.table("email_templates").delete().neq("id", 0).execute()
        flash('Todos os templates de email foram removidos da base de dados.', 'success')
        logger.info("All email templates cleared from database")
    except Exception as e:
        flash(f'Erro ao limpar templates: {str(e)}', 'danger')
        logger.error(f"Error clearing email templates: {str(e)}")

    return redirect(url_for('index'))

# ------------Flask Routes-----------
@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    mensagem = None
    if request.method == 'POST':
        email = request.form['email']
        logger.info(f"Registration attempt for email: {email}")

        existing_client = supabase.table("clientes").select("*").eq("email", email).execute()
        if existing_client.data:
            mensagem = f"Email {email} already registered"
        else:
            desconto_str = request.form.get('desconto', '')
            try:
                desconto = float(desconto_str)
            except (ValueError, TypeError):
                desconto = 0.0

            # Handle gastos field (admin only)
            gastos = 0.0
            if session.get('is_admin'):
                gastos_str = request.form.get('gastos', '')
                try:
                    gastos = float(gastos_str) if gastos_str else 0.0
                except (ValueError, TypeError):
                    gastos = 0.0

            supabase.table("clientes").insert({
                "adicionado_por": session.get('username', 'desconhecido'),
                "nome": request.form['nome'],
                "num_mergulho": int(request.form['num_mergulho']),
                "email": email,
                "data_mergulho": request.form['data_mergulho'],
                "valor_fatura": float(request.form['valor_fatura']),
                "desconto": desconto,
                "iva": float(request.form.get('iva', 22)) / 100,
                "nacionalidade": request.form.get('nacionalidade', 'portugues'),
                "gastos": gastos,
                "primeiro_email_enviado": False,
                "segundo_email_enviado": False,
                "email_manual_enviado": False,
                "receita": float(request.form['receita'])

            }).execute()
            return redirect(url_for('index'))

    clientes = supabase.table("clientes").select("*").execute().data
    for cliente in clientes:
        if isinstance(cliente['data_mergulho'], str):
            cliente['formatted_date'] = datetime.strptime(
                cliente['data_mergulho'],
                '%Y-%m-%d'
            ).strftime('%d/%m/%Y')
        else:
            cliente['formatted_date'] = cliente['data_mergulho'].strftime('%d/%m/%Y')

    return render_template("formulario_clientes.html", clientes=clientes, mensagem=mensagem)


# --------Send Email Manually---------
@app.route('/enviar/<email>', methods=['POST'])
def enviar_manual(email):
    try:
        # Fetch client from Supabase
        response = supabase.table("clientes").select("*").eq("email", email).execute()
        if not response.data:
            return 'Cliente não encontrado', 404

        cliente = response.data[0]

        # Check if email was already sent
        if cliente['email_manual_enviado']:
            logger.info(f'MANUAL: Email já enviado para {email}')
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return 'Email já enviado', 400
            return redirect(url_for('index'))

        logger.info(f'MANUAL: Sending email to {email}')
        if email_feedback(cliente, 'primeiro'):
            # Update in Supabase
            supabase.table("clientes").update({"email_manual_enviado": True}).eq("email", email).execute()

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return '', 204
            logger.info('MANUAL: Email enviado com sucesso!')
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return 'Falha ao enviar email', 400
            logger.info('MANUAL: Falha ao enviar email')

    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return str(e), 500
        logger.info(f'Erro: {str(e)}')

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return '', 204
    return redirect(url_for('index'))


# --------Get Email Template---------
@app.route('/get-email-template/<email>')
def get_email_template(email):
    try:
        response = supabase.table("clientes").select("*").eq("email", email).execute()
        if not response.data:
            return {'error': 'Cliente não encontrado'}, 404

        cliente = response.data[0]

        # Get template content using the new system
        content = get_email_template_content(cliente['nacionalidade'], 'primeiro')
        content = content.replace('[NOME]', cliente['nome'])

        return {'content': content}

    except Exception as e:
        logger.error(f"Error getting email template: {str(e)}")
        return {'error': str(e)}, 500


# --------Send Custom Email---------
@app.route('/enviar-email-personalizado', methods=['POST'])
def enviar_email_personalizado():
    try:
        email = request.form.get('email')
        subject = request.form.get('subject')
        content = request.form.get('content')

        # Get file attachments
        attachments = []
        for key in request.files:
            if key.startswith('attachment_'):
                file = request.files[key]
                if file and file.filename:
                    attachments.append(file)

        # Fetch client from Supabase
        response = supabase.table("clientes").select("*").eq("email", email).execute()
        if not response.data:
            return 'Cliente não encontrado', 404

        cliente = response.data[0]

        # Check if email was already sent
        if cliente['email_manual_enviado']:
            logger.info(f'Email já enviado para {email}')
            return 'Email já enviado', 400

        # Send the custom email with attachments
        if enviar_email_personalizado_aux(cliente['email'], subject, content, attachments):
            # Update in Supabase
            supabase.table("clientes").update({"email_manual_enviado": True}).eq("email", email).execute()
            logger.info(f'Email personalizado enviado com sucesso para {email} com {len(attachments)} anexos')
            return '', 204
        else:
            logger.error(f'Falha ao enviar email personalizado para {email}')
            return 'Falha ao enviar email', 400

    except Exception as e:
        logger.error(f'Erro ao enviar email personalizado: {str(e)}')
        return str(e), 500


def enviar_email_personalizado_aux(destinatario, assunto, conteudo, attachments=None):
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.image import MIMEImage

        # Create multipart message
        msg = MIMEMultipart("mixed")
        msg['From'] = app.config['SMTP_USERNAME']
        msg['To'] = destinatario
        msg['Subject'] = assunto

        # Add headers for better email client compatibility
        msg.add_header('X-Mailer', 'Atlantic Diving Center CRM')

        # Create HTML part
        html_part = MIMEText(conteudo, "html", "utf-8")
        msg.attach(html_part)

        # Attach all files
        if attachments:
            for attachment in attachments:
                try:
                    # Read file data
                    file_data = attachment.read()

                    # Create MIME attachment
                    mime_attachment = MIMEImage(file_data, _subtype='jpeg')  # Default to jpeg
                    mime_attachment.add_header('Content-Disposition', 'attachment', filename=attachment.filename)

                    # Attach to message
                    msg.attach(mime_attachment)

                    # Reset file pointer for potential future reads
                    attachment.seek(0)

                except Exception as attach_error:
                    logger.error(f"Error attaching file {attachment.filename}: {str(attach_error)}")

        # Send email
        with smtplib.SMTP_SSL(app.config['SMTP_SERVER'], app.config['SMTP_PORT']) as server:
            server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
            server.send_message(msg)

        logger.info(
            f"Email sent successfully to {destinatario} with {len(attachments) if attachments else 0} attachments")
        return True

    except Exception as e:
        logger.error(f"Email personalizado failed: {str(e)}")
        return False


# ---------Remover---------
@app.route('/remover/<email>', methods=['POST'])
def remover_cliente(email):
    try:
        # Delete from Supabase
        supabase.table("clientes").delete().eq("email", email).execute()
        return '', 204  # Successful deletion returns no content
    except Exception as e:
        return str(e), 500

# ---------Update Gastos---------
@app.route('/update-gastos', methods=['POST'])
@login_required
def update_gastos():
    """Update gastos and receita for a client (admin only)"""
    if not session.get('is_admin'):
        return {'success': False, 'error': 'Unauthorized'}, 403

    try:
        data = request.get_json()
        email = data.get('email')
        gastos = float(data.get('gastos', 0.00))

        if not email:
            return {'success': False, 'error': 'Email is required'}

        # Buscar valor_final atual do cliente
        resultado = supabase.table("clientes").select("valor_fatura").eq("email", email).execute()
        dados = resultado.data

        if not dados:
            return {'success': False, 'error': 'Cliente não encontrado'}

        valor_fatura = dados[0]["valor_fatura"]
        receita = valor_fatura - gastos

        # Atualizar gastos e receita
        supabase.table("clientes").update({
            "gastos": gastos,
            "receita": receita
        }).eq("email", email).execute()

        logger.info(f"Gastos e receita atualizados para {email}: gastos={gastos}, receita={receita}")
        return {'success': True}


    except Exception as e:
        logger.error(f"Erro ao atualizar gastos e receita: {str(e)}")
        return {'success': False, 'error': str(e)}

# -------Send Email to All-----------
@app.route('/enviar-todos', methods=['POST'])
def enviar_manual_todos():
    try:
        # Fetch all clients from Supabase
        response = supabase.table("clientes").select("*").execute()
        clientes = response.data

        emails_sent = 0
        for cliente in clientes:
            # Only send if manual email hasn't been sent yet
            if not cliente['email_manual_enviado']:
                if email_feedback(cliente, 'primeiro'):
                    supabase.table("clientes").update({"email_manual_enviado": True}).eq("email",
                                                                                         cliente["email"]).execute()
                    logger.info(f'Email enviado com sucesso para {cliente["email"]}')
                    emails_sent += 1
                else:
                    logger.error(f'Falha ao enviar email para {cliente["email"]}')
            else:
                logger.info(f'Email já enviado para {cliente["email"]}, pulando...')

        logger.info(f'Emails enviados para {emails_sent} clientes')
        return redirect(url_for('index'))

    except Exception as e:
        logger.error(f'Erro ao enviar emails: {str(e)}')
        return redirect(url_for('index'))

# --------Debug----------
@app.route('/debug/<email>')
def debug_cliente(email):
    response = supabase.table("clientes").select("*").eq("email", email).execute()
    if not response.data:
        return 'Cliente não encontrado', 404

    cliente = response.data[0]
    data_mergulho = datetime.strptime(cliente["data_mergulho"], "%Y-%m-%d").date()

    return {
        'nome': cliente["nome"],
        'primeiro_email': cliente["primeiro_email_enviado"],
        'segundo_email': cliente["segundo_email_enviado"],
        'email_manual': cliente["email_manual_enviado"],
        'data_mergulho': str(data_mergulho),
        'dias_passados': (datetime.now().date() - data_mergulho).days
    }

# --------Table Refreshing------------
@app.route('/atualizar-tabela')
def atualizar_tabela():
    clientes = supabase.table("clientes").select("*").execute().data
    for cliente in clientes:
        if isinstance(cliente['data_mergulho'], str):
            cliente['formatted_date'] = datetime.strptime(
                cliente['data_mergulho'],
                '%Y-%m-%d'
            ).strftime('%d/%m/%Y')
        else:
            cliente['formatted_date'] = cliente['data_mergulho'].strftime('%d/%m/%Y')
    return render_template("partials/tabela_clientes.html", clientes=clientes)


# --------Send Email to All------------
@app.route('/exportar-emails')
def exportar_emails():
    try:
        response = supabase.table("clientes").select("*").execute()
        clientes = response.data
        clientes_data = [{
            'Adicionado por': cliente["adicionado_por"],
            'Nome': cliente["nome"],
            'Email': cliente["email"],
            'Nº Mergulhos': cliente["num_mergulho"],
            'Data Mergulho': datetime.strptime(cliente["data_mergulho"], "%Y-%m-%d").strftime('%Y/%m/%d'),
            'Nacionalidade': cliente["nacionalidade"].capitalize(),
            '1º Email Enviado': 'Sim' if cliente["primeiro_email_enviado"] else 'Não',
            '2º Email Enviado': 'Sim' if cliente["segundo_email_enviado"] else 'Não',
            'Email Manual': 'Sim' if cliente["email_manual_enviado"] else 'Não',
            'Desconto (%)': cliente["desconto"],
            'Valor(€)': cliente["valor_fatura"],
            'Valor com Iva(€)': cliente["valor_fatura"] * (1 + cliente["iva"]),
            'Valor de IVA(€)': cliente["valor_fatura"] * cliente["iva"],
            'Gastos(€)': cliente.get("gastos", 0) or 0,
            'Receita(€)': cliente["receita"]
        } for cliente in clientes]

        df = pd.DataFrame(clientes_data)
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Clientes')
            workbook = writer.book
            worksheet = writer.sheets['Clientes']

            for row in worksheet.iter_rows(min_row=2, min_col=11, max_col=15):
                for cell in row:
                     cell.number_format = '#,##0.00" €"'
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = (max_length + 5)  # instead of +2
                worksheet.column_dimensions[column_letter].width = adjusted_width

            # Center all cells
            for row in worksheet.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(horizontal='right', vertical='center')

        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='Clientes_Atlantic_Diving_Center.xlsx'
        )

    except Exception as e:
        logger.error(f"Erro ao exportar emails: {str(e)}")
        return redirect(url_for('index'))

#--------------Get emails from excel files--------------
@app.route('/upload-excel-emails', methods=['POST'])
@login_required
def upload_excel_emails():
    """Upload Excel file and extract emails from a column"""
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    try:
        if 'excel_file' not in request.files:
            flash('Nenhum arquivo selecionado.', 'danger')
            return redirect(url_for('marketing_emails'))

        file = request.files['excel_file']
        if file.filename == '':
            flash('Nenhum arquivo selecionado.', 'danger')
            return redirect(url_for('marketing_emails'))

        if not file.filename.endswith(('.xlsx', '.xls')):
            flash('Por favor, selecione um arquivo Excel (.xlsx ou .xls).', 'danger')
            return redirect(url_for('marketing_emails'))

        # Read the Excel file
        df = pd.read_excel(file)

        # Get column name from form
        column_name = request.form.get('email_column', '').strip()

        if not column_name:
            flash('Por favor, especifique o nome da coluna que contém os emails.', 'danger')
            return redirect(url_for('marketing_emails'))

        # Check if column exists
        if column_name not in df.columns:
            available_columns = ', '.join(df.columns.tolist())
            flash(f'Coluna "{column_name}" não encontrada. Colunas disponíveis: {available_columns}', 'danger')
            return redirect(url_for('marketing_emails'))

        # Extract emails from the specified column
        emails = []
        for email in df[column_name].dropna():
            email_str = str(email).strip()
            if email_str and '@' in email_str:
                emails.append(email_str)

        if not emails:
            flash('Nenhum email válido encontrado na coluna especificada.', 'warning')
            return redirect(url_for('marketing_emails'))

        # Save emails to the marketing emails file
        marketing_emails_file = 'marketing_emails.txt'
        with open(marketing_emails_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(emails))

        flash(f'✅ {len(emails)} emails importados com sucesso do arquivo Excel!', 'success')
        logger.info(f"Imported {len(emails)} emails from Excel file: {file.filename}")

        return redirect(url_for('marketing_emails'))

    except Exception as e:
        flash(f'Erro ao processar arquivo Excel: {str(e)}', 'danger')
        logger.error(f"Error processing Excel file: {str(e)}")
        return redirect(url_for('marketing_emails'))

#---------View collumns from excel files---------
@app.route('/preview-excel-columns', methods=['POST'])
@login_required
def preview_excel_columns():
    """Preview Excel file columns without saving emails"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        if 'excel_file' not in request.files:
            return {'error': 'Nenhum arquivo selecionado.'}, 400

        file = request.files['excel_file']
        if file.filename == '':
            return {'error': 'Nenhum arquivo selecionado.'}, 400

        if not file.filename.endswith(('.xlsx', '.xls')):
            return {'error': 'Por favor, selecione um arquivo Excel (.xlsx ou .xls).'}, 400

        # Read the Excel file
        df = pd.read_excel(file)

        # Get column names and first few values
        columns_info = []
        for col in df.columns:
            # Get first 5 non-null values from the column
            sample_values = df[col].dropna().head(5).tolist()
            # Convert numpy.int64 to regular Python int for JSON serialization
            non_null_count = int(df[col].notna().sum())
            total_rows = int(len(df))

            columns_info.append({
                'name': col,
                'sample_values': sample_values,
                'total_rows': total_rows,
                'non_null_count': non_null_count
            })

        return {
            'columns': columns_info,
            'filename': file.filename
        }

    except Exception as e:
        logger.error(f"Error previewing Excel file: {str(e)}")
        return {'error': f'Erro ao processar arquivo: {str(e)}'}, 500

#---------Admin managing users-----------
@app.route('/admin/users', methods=['GET', 'POST'])
@login_required
def manage_users():
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    mensagem = None

    # Handle user creation
    if request.method == 'POST' and 'create_user' in request.form:
        username = request.form['username']
        password = request.form['password']
        is_admin = bool(int(request.form.get('is_admin', 0)))
        password_hash = password
        try:
            supabase.table("usuarios").insert({
                "username": username,
                "password_hash": password_hash,
                "is_admin": is_admin
            }).execute()
            mensagem = "Usuário criado com sucesso!"
        except Exception as e:
            mensagem = f"Erro ao criar usuário: {e}"

    # Handle user deletion
    if request.method == 'POST' and 'delete_user' in request.form:
        user_id = int(request.form['delete_user'])
        try:
            supabase.table("usuarios").delete().eq("id", user_id).execute()
            mensagem = "Usuário removido com sucesso!"
        except Exception as e:
            mensagem = f"Erro ao remover usuário: {e}"

    users = supabase.table("usuarios").select("*").execute().data
    return render_template("admin_users.html", users=users, mensagem=mensagem)


# ----------Login-------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        entered_username = request.form.get('username', '').strip()
        entered_password = request.form.get('password', '')
        if not entered_username or not entered_password:
            flash('Por favor, preencha todos os campos.', 'danger')
            return render_template('login.html')

        user_data = supabase.table("usuarios").select("*").eq("username", entered_username).execute().data
        if user_data and user_data[0]['password_hash'] == entered_password:
            session['logged_in'] = True
            session['username'] = entered_username
            session['is_admin'] = bool(user_data[0]['is_admin'])
            return redirect(url_for('index'))
        else:
            logger.info('Invalid credentials')
            flash('Usuário ou senha inválidos', 'danger')
    return render_template('login.html')

# -------Iva setter--------
@app.route('/set-iva', methods=['POST'])
@login_required
def set_iva():
    new_iva = request.json.get('iva')
    supabase.table("configuracoes").upsert({"chave": "iva", "valor": str(new_iva)}).execute()
    return {'success': True}

# --------Edit Email Templates---------
@app.route('/editar-primeiro-email', methods=['GET', 'POST'])
@login_required
def editar_primeiro_email():
    logger.info(f"editar_primeiro_email route called with method: {request.method}")
    if not session.get('is_admin'):
        logger.info("User is not admin")
        return redirect(url_for('index'))

    try:
        logger.info("Getting template content for primeiro email")
        # Get template content using the same logic as get_email_template_content
        template_content = {}
        nacionalidades = ['português', 'inglês', 'francês', 'alemão']
        import re  # Move import to top of function

        for nacionalidade in nacionalidades:
            try:
                # Always load from file first
                template_files = {
                    'português': 'email_feedback.html',
                    'inglês': 'email_feedback_internacional_ingles.html',
                    'francês': 'email_feedback_internacional_frances.html',
                    'alemão': 'email_feedback_internacional_alemao.html',
                    'dinamarques': "Tak for din dykkeroplevelse!",
                    'espanhol': "¡Gracias por tu experiencia de buceo!",
                    'noruegues': "Takk for din dykkeopplevelse!",
                    'polaco': "Dziękujemy za Twoje doświadczenie nurkowe!",
                    'sueco': "Tack för din dykupplevelse!",
                }
                template_file = template_files.get(nacionalidade, 'email_feedback.html')

                with app.app_context():
                    full_template = render_template(template_file, nome="[NOME]")
                    # Extract only the body content for the editor
                    body_match = re.search(r'<body[^>]*>(.*?)</body>', full_template, re.DOTALL | re.IGNORECASE)
                    if body_match:
                        template_content[nacionalidade] = body_match.group(1).strip()
                    else:
                        # Fallback to full template if no body tag found
                        template_content[nacionalidade] = full_template
                logger.info(f"Loaded template from file for {nacionalidade}")

            except Exception as e:
                logger.error(f"Error loading template for {nacionalidade}: {str(e)}")
                # Fallback template content
                template_content[
                    nacionalidade] = f"<p>Olá [NOME],</p><p>Obrigado pela sua experiência de mergulho!</p><p>Atenciosamente,<br>Atlantic Diving Center</p>"

        # Store in session for the edit page
        session['editing_template'] = 'primeiro'
        session['template_content'] = template_content

        logger.info("Redirecting to edit_email_template")
        return redirect(url_for('edit_email_template'))

    except Exception as e:
        logger.error(f"Erro ao editar primeiro email: {str(e)}")
        flash('Erro ao abrir editor de email', 'danger')
        return redirect(url_for('index'))

# -----------Edit Second Email------------
@app.route('/editar-segundo-email', methods=['GET', 'POST'])
@login_required
def editar_segundo_email():
    logger.info(f"editar_segundo_email route called with method: {request.method}")
    if not session.get('is_admin'):
        logger.info("User is not admin")
        return redirect(url_for('index'))

    try:
        logger.info("Getting template content for segundo email")
        # Get template content using the same logic as get_email_template_content
        template_content = {}
        nacionalidades = ['português', 'inglês', 'francês', 'alemão']
        import re  # Move import to top of function

        for nacionalidade in nacionalidades:
            try:
                # Always load from file first
                template_files = {
                    'português': 'email_feedback.html',
                    'inglês': 'email_feedback_internacional_ingles.html',
                    'francês': 'email_feedback_internacional_frances.html',
                    'alemão': 'email_feedback_internacional_alemao.html',
                    'dinamarques': "Tak for din dykkeroplevelse!",
                    'espanhol': "¡Gracias por tu experiencia de buceo!",
                    'noruegues': "Takk for din dykkeopplevelse!",
                    'polaco': "Dziękujemy za Twoje doświadczenie nurkowe!",
                    'sueco': "Tack för din dykupplevelse!",
                }
                template_file = template_files.get(nacionalidade, 'email_feedback.html')

                with app.app_context():
                    full_template = render_template(template_file, nome="[NOME]")
                    # Extract only the body content for the editor
                    body_match = re.search(r'<body[^>]*>(.*?)</body>', full_template, re.DOTALL | re.IGNORECASE)
                    if body_match:
                        template_content[nacionalidade] = body_match.group(1).strip()
                    else:
                        # Fallback to full template if no body tag found
                        template_content[nacionalidade] = full_template
                logger.info(f"Loaded template from file for {nacionalidade}")

            except Exception as e:
                logger.error(f"Error loading template for {nacionalidade}: {str(e)}")
                # Fallback template content
                template_content[
                    nacionalidade] = f"<p>Olá [NOME],</p><p>Obrigado pela sua experiência de mergulho!</p><p>Atenciosamente,<br>Atlantic Diving Center</p>"

        # Store in session for the edit page
        session['editing_template'] = 'segundo'
        session['template_content'] = template_content

        logger.info("Redirecting to edit_email_template")
        return redirect(url_for('edit_email_template'))

    except Exception as e:
        logger.error(f"Erro ao editar segundo email: {str(e)}")
        flash('Erro ao abrir editor de email', 'danger')
        return redirect(url_for('index'))

#-------------Edit templates---------------
@app.route('/edit-email-template', methods=['GET', 'POST'])
@login_required
def edit_email_template():
    logger.info("edit_email_template route called")
    if not session.get('is_admin'):
        logger.info("User is not admin")
        return redirect(url_for('index'))

    if request.method == 'POST':
        try:
            # Get the updated content from the form
            portugues_content = request.form.get('portugues_content', '')
            ingles_content = request.form.get('ingles_content', '')
            frances_content = request.form.get('frances_content', '')
            alemao_content = request.form.get('alemao_content', '')
            dinamarques_content = request.form.get('dinamarques_content', '')
            espanhol_content = request.form.get('espanhol_content', '')
            noruegues_content = request.form.get('noruegues_content', '')
            polaco_content = request.form.get('polaco_content', '')
            sueco_content = request.form.get('sueco_content', '')

            editing_template = session.get('editing_template', 'primeiro')

            # Check if user wants to save custom templates or reset to defaults
            save_custom = request.form.get('save_custom', 'false') == 'true'

            if save_custom:
                # Save templates to database (only if content is not empty)
                templates_to_save = []

                if portugues_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'português', 'tipo': editing_template, 'conteudo': portugues_content})
                if ingles_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'inglês', 'tipo': editing_template, 'conteudo': ingles_content})
                if frances_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'francês', 'tipo': editing_template, 'conteudo': frances_content})
                if alemao_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'alemão', 'tipo': editing_template, 'conteudo': alemao_content})
                if dinamarques_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'dinamarques', 'tipo': editing_template, 'conteudo': dinamarques_content})
                if espanhol_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'espanhol', 'tipo': editing_template, 'conteudo': espanhol_content})
                if noruegues_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'noruegues', 'tipo': editing_template, 'conteudo': noruegues_content})
                if polaco_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'polaco', 'tipo': editing_template, 'conteudo': polaco_content})
                if sueco_content.strip():
                    templates_to_save.append(
                        {'nacionalidade': 'sueco', 'tipo': editing_template, 'conteudo': sueco_content})

                # Delete existing templates for this type
                supabase.table("email_templates").delete().eq("tipo", editing_template).execute()

                # Insert new templates (only if there are any to save)
                if templates_to_save:
                    supabase.table("email_templates").insert(templates_to_save).execute()
                    flash(
                        f'Templates personalizados salvos com sucesso! {len(templates_to_save)} template(s) personalizado(s).',
                        'success')
                else:
                    flash('Nenhum template personalizado foi salvo.', 'info')
            else:
                # User wants to reset to defaults - clear database entries
                supabase.table("email_templates").delete().eq("tipo", editing_template).execute()
                flash('Templates resetados para os padrões dos arquivos.', 'success')
            return redirect(url_for('index'))

        except Exception as e:
            logger.error(f"Erro ao salvar templates: {str(e)}")
            flash('Erro ao salvar templates', 'danger')

    # Get the template content from session or database
    editing_template = session.get('editing_template', 'primeiro')
    template_content = {}

    # Always load templates from files
    nacionalidades = ['português', 'inglês', 'francês', 'alemão']
    import re  # Move import to top of function

    for nacionalidade in nacionalidades:
        try:
            # Always load from file first
            template_files = {
                'português': 'email_feedback.html',
                'inglês': 'email_feedback_internacional_ingles.html',
                'francês': 'email_feedback_internacional_frances.html',
                'alemão': 'email_feedback_internacional_alemao.html',
                'dinamarques': 'email_feedback_internacional_dinamarques.html',
                'espanhol': 'email_feedback_internacional_espanhol.html',
                'noruegues': 'email_feedback_internacional_noruegues.html',
                'polaco': 'email_feedback_internacional_polaco.html',
                'sueco': 'email_feedback_internacional_sueco.html',
            }
            template_file = template_files.get(nacionalidade, 'email_feedback.html')

            with app.app_context():
                full_template = render_template(template_file, nome="[NOME]")
                # Extract only the body content for the editor
                body_match = re.search(r'<body[^>]*>(.*?)</body>', full_template, re.DOTALL | re.IGNORECASE)
                if body_match:
                    template_content[nacionalidade] = body_match.group(1).strip()
                    logger.info(
                        f"Extracted body content for {nacionalidade}: {len(template_content[nacionalidade])} chars")
                else:
                    # Fallback to full template if no body tag found
                    template_content[nacionalidade] = full_template
                    logger.info(
                        f"Using full template for {nacionalidade}: {len(template_content[nacionalidade])} chars")
            logger.info(f"Loaded template from file for {nacionalidade} ({editing_template})")

            # Check for custom template in database
            try:
                response = supabase.table("email_templates").select("*").eq("nacionalidade", nacionalidade).eq("tipo",
                                                                                                               editing_template).execute()
                if response.data and response.data[0]['conteudo'].strip():
                    template_content[nacionalidade] = response.data[0]['conteudo']
                    logger.info(f"Loaded custom template from database for {nacionalidade}")
            except Exception as db_error:
                logger.error(f"Error loading custom template for {nacionalidade}: {str(db_error)}")

        except Exception as e:
            logger.error(f"Error getting template for {nacionalidade}: {str(e)}")
            template_content[
                nacionalidade] = f"<p>Olá [NOME],</p><p>Obrigado pela sua experiência de mergulho!</p><p>Atenciosamente,<br>Atlantic Diving Center</p>"

    # All templates are loaded from files, so they're all "default"
    template_status = {
        'português': 'default',
        'inglês': 'default',
        'francês': 'default',
        'alemão': 'default',
        'dinamarques': 'default',
        'espanhol': 'default',
        'noruegues': 'default',
        'polaco': 'default',
        'sueco': 'default',
    }

    # Debug: Print template content lengths
    for nacionalidade, content in template_content.items():
        logger.info(f"Template {nacionalidade}: {len(content)} chars")
        logger.info(f"First 100 chars: {content[:100]}...")
        logger.info(f"Last 100 chars: {content[-100:] if len(content) > 100 else content}")

    return render_template('edit_email_template.html',
                           template_content=template_content,
                           editing_template=editing_template,
                           template_status=template_status)

#--------------Marketing emails functions---------------
@app.route('/marketing-emails', methods=['GET', 'POST'])
@login_required
def marketing_emails():
    """Marketing email interface for bulk sending"""
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    # File to store marketing emails
    marketing_emails_file = 'marketing_emails.txt'

    if request.method == 'POST':
        try:
            # Get the marketing email content
            subject = request.form.get('subject', '')
            content = request.form.get('content', '')
            bulk_emails_text = request.form.get('bulk_emails', '')

            if not subject or not content:
                flash('Por favor, preencha o assunto e o conteúdo do email.', 'danger')
                return redirect(url_for('marketing_emails'))

            # Save bulk emails to file
            if bulk_emails_text.strip():
                # Clean up the email text before saving
                cleaned_emails = []
                lines = bulk_emails_text.strip().split('\n')
                for line in lines:
                    if line.strip():
                        # Split by commas if present
                        if ',' in line:
                            emails_in_line = [email.strip() for email in line.split(',') if email.strip()]
                            cleaned_emails.extend(emails_in_line)
                        else:
                            cleaned_emails.append(line.strip())

                # Save one email per line, no extra spaces
                with open(marketing_emails_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(cleaned_emails))
                logger.info(f"Marketing emails saved to {marketing_emails_file}")

            # Parse bulk emails
            bulk_emails = []
            if bulk_emails_text.strip():
                # Split by newlines first, then by commas
                lines = bulk_emails_text.strip().split('\n')
                for line in lines:
                    if line.strip():
                        # Split by commas if present
                        if ',' in line:
                            emails_in_line = [email.strip() for email in line.split(',') if email.strip()]
                            bulk_emails.extend(emails_in_line)
                        else:
                            bulk_emails.append(line.strip())

            # Get clients from database if requested
            database_emails = []
            if request.form.get('include_database') == 'on':
                response = supabase.table("clientes").select("*").execute()
                database_emails = [client['email'] for client in response.data]

            # Combine all email addresses
            all_emails = list(set(bulk_emails + database_emails))  # Remove duplicates

            if not all_emails:
                flash('Nenhum destinatário encontrado. Adicione emails ou marque "Incluir clientes da base de dados".',
                      'warning')
                return redirect(url_for('marketing_emails'))

            # Get file attachments
            attachments = []
            for key in request.files:
                if key.startswith('attachment_'):
                    file = request.files[key]
                    if file and file.filename:
                        attachments.append(file)

            # Send marketing email to all recipients
            emails_sent = 0
            failed_emails = []

            for email in all_emails:
                try:
                    # Send the email with attachments
                    if enviar_email_personalizado_aux(email, subject, content, attachments):
                        emails_sent += 1
                        logger.info(f"Marketing email sent to {email}")
                    else:
                        failed_emails.append(email)
                        logger.error(f"Failed to send marketing email to {email}")

                except Exception as e:
                    failed_emails.append(email)
                    logger.error(f"Failed to send marketing email to {email}: {str(e)}")

            # Show results
            if emails_sent > 0:
                flash(f'Marketing email enviado com sucesso para {emails_sent} destinatários!', 'success')
            if failed_emails:
                flash(f'Falha ao enviar para {len(failed_emails)} emails: {", ".join(failed_emails)}', 'warning')

            return redirect(url_for('marketing_emails'))

        except Exception as e:
            flash(f'Erro ao enviar marketing emails: {str(e)}', 'danger')
            logger.error(f"Marketing email error: {str(e)}")

    # Get client count for display
    response = supabase.table("clientes").select("*").execute()
    client_count = len(response.data)

    # Get marketing email lists from Supabase
    email_lists = []
    try:
        lists_response = supabase.table("marketing_email_lists").select("*").execute()

        # Group emails by list name
        lists = {}
        for record in lists_response.data:
            list_name = record['list_name']
            if list_name not in lists:
                lists[list_name] = []
            lists[list_name].append(record['email'])

        # Convert to format for frontend
        for list_name, emails in lists.items():
            email_lists.append({
                'list_name': list_name,
                'email_count': len(emails),
                'emails': emails
            })

        logger.info(f"Loaded {len(email_lists)} marketing email lists from Supabase")
    except Exception as e:
        logger.error(f"Error loading marketing email lists: {str(e)}")

    return render_template('marketing_emails.html', client_count=client_count, email_lists=email_lists)

#---------------Remove marketing emails-----------------
@app.route('/clear-marketing-emails', methods=['POST'])
@login_required
def clear_marketing_emails():
    """Clear the saved marketing emails file"""
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    try:
        marketing_emails_file = 'marketing_emails.txt'
        if os.path.exists(marketing_emails_file):
            os.remove(marketing_emails_file)
            flash('Lista de emails de marketing foi limpa com sucesso.', 'success')
            logger.info(f"Marketing emails file {marketing_emails_file} cleared")
        else:
            flash('Nenhum arquivo de emails encontrado para limpar.', 'info')
    except Exception as e:
        flash(f'Erro ao limpar arquivo de emails: {str(e)}', 'danger')
        logger.error(f"Error clearing marketing emails file: {str(e)}")

    return redirect(url_for('marketing_emails'))

#--------------Get email lists----------------
@app.route('/get-marketing-email-lists', methods=['GET'])
@login_required
def get_marketing_email_lists():
    """Get all marketing email lists from Supabase"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        response = supabase.table("marketing_email_lists").select("*").execute()

        # Group emails by list name
        lists = {}
        for record in response.data:
            list_name = record['list_name']
            if list_name not in lists:
                lists[list_name] = []
            lists[list_name].append(record['email'])

        # Convert to format for frontend
        email_lists = []
        for list_name, emails in lists.items():
            email_lists.append({
                'list_name': list_name,
                'email_count': len(emails),
                'emails': emails
            })

        return {'lists': email_lists}

    except Exception as e:
        logger.error(f"Error getting marketing email lists: {str(e)}")
        return {'error': f'Erro ao carregar listas: {str(e)}'}, 500

#--------------Delete emails from marketing lists--------------
@app.route('/delete-marketing-email-list', methods=['POST'])
@login_required
def delete_marketing_email_list():
    """Delete a marketing email list from Supabase"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        list_name = request.form.get('list_name', '')
        if not list_name:
            return {'error': 'Nome da lista não especificado'}, 400

        # Delete the list
        supabase.table("marketing_email_lists").delete().eq("list_name", list_name).execute()

        logger.info(f"Deleted marketing email list: {list_name}")
        return {'success': True, 'message': f'Lista "{list_name}" removida com sucesso'}

    except Exception as e:
        logger.error(f"Error deleting marketing email list: {str(e)}")
        return {'error': f'Erro ao remover lista: {str(e)}'}, 500

#-------------Editing marketing email lists----------------
@app.route('/marketing-email-editor', methods=['GET'])
@login_required
def marketing_email_editor():
    """Web-based Excel editor for marketing email lists"""
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    # Get all existing lists
    email_lists = []
    try:
        lists_response = supabase.table("marketing_email_lists").select("*").execute()

        # Group emails by list name
        lists = {}
        for record in lists_response.data:
            list_name = record['list_name']
            if list_name not in lists:
                lists[list_name] = []
            lists[list_name].append(record['email'])

        # Convert to format for frontend
        for list_name, emails in lists.items():
            email_lists.append({
                'list_name': list_name,
                'email_count': len(emails),
                'emails': emails
            })

        logger.info(f"Loaded {len(email_lists)} marketing email lists for editor")
    except Exception as e:
        logger.error(f"Error loading marketing email lists for editor: {str(e)}")

    return render_template('marketing_email_editor.html', email_lists=email_lists)

#---------Storing email lists----------
@app.route('/api/marketing-lists', methods=['GET'])
@login_required
def get_marketing_lists_api():
    """API endpoint to get all marketing lists"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        response = supabase.table("marketing_email_lists").select("*").execute()

        # Group emails by list name
        lists = {}
        for record in response.data:
            list_name = record['list_name']
            if list_name not in lists:
                lists[list_name] = []
            lists[list_name].append(record['email'])

        # Convert to format for frontend
        email_lists = []
        for list_name, emails in lists.items():
            email_lists.append({
                'list_name': list_name,
                'email_count': len(emails),
                'emails': emails
            })

        return {'lists': email_lists}

    except Exception as e:
        logger.error(f"Error getting marketing lists API: {str(e)}")
        return {'error': f'Erro ao carregar listas: {str(e)}'}, 500


@app.route('/api/marketing-list/<list_name>', methods=['GET'])
@login_required
def get_marketing_list_api(list_name):
    """API endpoint to get a specific marketing list"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        response = supabase.table("marketing_email_lists").select("*").eq("list_name", list_name).execute()

        emails = [record['email'] for record in response.data]

        return {
            'list_name': list_name,
            'emails': emails,
            'email_count': len(emails)
        }

    except Exception as e:
        logger.error(f"Error getting marketing list API: {str(e)}")
        return {'error': f'Erro ao carregar lista: {str(e)}'}, 500


@app.route('/api/marketing-list', methods=['POST'])
@login_required
def save_marketing_list_api():
    """API endpoint to save/update a marketing list"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        data = request.get_json()
        list_name = data.get('list_name', '').strip()
        emails = data.get('emails', [])

        if not list_name:
            return {'error': 'Nome da lista é obrigatório'}, 400

        # Validate emails
        valid_emails = []
        for email in emails:
            email = email.strip()
            if email and '@' in email and '.' in email.split('@')[1]:
                valid_emails.append(email)

        # Remove duplicates
        valid_emails = list(set(valid_emails))

        # Delete existing list
        supabase.table("marketing_email_lists").delete().eq("list_name", list_name).execute()

        # Insert new emails
        if valid_emails:
            email_records = []
            for email in valid_emails:
                email_records.append({
                    'list_name': list_name,
                    'email': email,
                    'created_at': datetime.now().isoformat()
                })
            supabase.table("marketing_email_lists").insert(email_records).execute()
        else:
            # Insert a placeholder record for the list with no emails
            supabase.table("marketing_email_lists").insert([{
                'list_name': list_name,
                'email': None,
                'created_at': datetime.now().isoformat()
            }]).execute()

        logger.info(f"Saved marketing list '{list_name}' with {len(valid_emails)} emails")
        return {
            'success': True,
            'message': f'Lista "{list_name}" salva com {len(valid_emails)} emails',
            'list_name': list_name,
            'email_count': len(valid_emails)
        }

    except Exception as e:
        logger.error(f"Error saving marketing list API: {str(e)}")
        return {'error': f'Erro ao salvar lista: {str(e)}'}, 500


@app.route('/api/marketing-list/<list_name>', methods=['DELETE'])
@login_required
def delete_marketing_list_api(list_name):
    """API endpoint to delete a marketing list"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        supabase.table("marketing_email_lists").delete().eq("list_name", list_name).execute()

        logger.info(f"Deleted marketing list: {list_name}")
        return {
            'success': True,
            'message': f'Lista "{list_name}" removida com sucesso'
        }

    except Exception as e:
        logger.error(f"Error deleting marketing list API: {str(e)}")
        return {'error': f'Erro ao remover lista: {str(e)}'}, 500

#-------------Upload emails from excel-------------
@app.route('/upload-marketing-emails-excel', methods=['POST'])
@login_required
def upload_marketing_emails_excel():
    """Upload Excel file with marketing emails and store in Supabase"""
    if not session.get('is_admin'):
        return {'error': 'Unauthorized'}, 403

    try:
        if 'excel_file' not in request.files:
            return {'error': 'Nenhum arquivo foi enviado'}, 400

        file = request.files['excel_file']
        if file.filename == '':
            return {'error': 'Nenhum arquivo foi selecionado'}, 400

        # Check file extension
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            return {'error': 'Por favor, selecione um arquivo Excel válido (.xlsx ou .xls)'}, 400

        # Read Excel file
        df = pd.read_excel(file)

        # Get email column from request
        email_column = request.form.get('email_column', '')
        if not email_column:
            return {'error': 'Coluna de email não especificada'}, 400

        if email_column not in df.columns:
            return {'error': f'Coluna "{email_column}" não encontrada no arquivo'}, 400

        # Extract emails from the specified column
        emails = df[email_column].dropna().astype(str).tolist()

        # Clean and validate emails
        valid_emails = []
        for email in emails:
            email = email.strip()
            if email and '@' in email and '.' in email.split('@')[1]:
                valid_emails.append(email)

        if not valid_emails:
            return {'error': 'Nenhum email válido encontrado no arquivo'}, 400

        # Get list name from request
        list_name = request.form.get('list_name', 'Lista de Marketing')
        if not list_name.strip():
            list_name = 'Lista de Marketing'

        # Store emails in Supabase
        try:
            # Check if list already exists and get existing emails
            existing_emails = []
            try:
                existing_result = supabase.table("marketing_email_lists").select("email").eq("list_name",
                                                                                             list_name).execute()
                existing_emails = [row['email'] for row in existing_result.data]
            except Exception:
                # List doesn't exist yet, that's fine
                pass

            # Filter out emails that already exist to avoid duplicates
            new_emails = []
            for email in valid_emails:
                if email not in existing_emails:
                    new_emails.append(email)

            if not new_emails:
                return {
                    'success': True,
                    'message': f'Todos os emails já existem na lista "{list_name}". Nenhum novo email adicionado.',
                    'count': 0,
                    'list_name': list_name,
                    'updated': False
                }

            # Insert only new emails
            email_records = []
            for email in new_emails:
                email_records.append({
                    'list_name': list_name,
                    'email': email,
                    'created_at': datetime.now().isoformat()
                })

            if email_records:
                supabase.table("marketing_email_lists").insert(email_records).execute()

            logger.info(
                f"Added {len(new_emails)} new marketing emails to list '{list_name}' (skipped {len(valid_emails) - len(new_emails)} duplicates)")
            return {
                'success': True,
                'message': f'{len(new_emails)} novos emails adicionados à lista "{list_name}" (duplicados ignorados)',
                'count': len(new_emails),
                'list_name': list_name,
                'updated': True
            }

        except Exception as db_error:
            logger.error(f"Database error: {str(db_error)}")
            return {'error': f'Erro ao salvar na base de dados: {str(db_error)}'}, 500

    except Exception as e:
        logger.error(f"Error uploading marketing emails Excel: {str(e)}")
        return {'error': f'Erro ao processar arquivo: {str(e)}'}, 500

@app.route('/marcar-email-manual/<email>', methods=['POST'])
def marcar_email_manual(email):
    resultado = supabase.table("clientes").select("*").eq("email", email).execute()
    dados = resultado.data

    if not dados:
        flash("Cliente não encontrado.", "danger")
        return redirect(url_for("index"))

    cliente = dados[0]

    if not cliente["email_manual_enviado"]:
        supabase.table("clientes").update({"email_manual_enviado": True}).eq("email", email).execute()
        flash("Email marcado como enviado com sucesso.", "success")
    else:
        flash("O email já estava marcado como enviado.", "info")

    return redirect(url_for("index"))


#-------Starter--------
if __name__ == '__main__':
    # Timer(3, open_browser).start()
    app.run()
