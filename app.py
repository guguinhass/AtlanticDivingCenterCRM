from flask import Flask, request, redirect, url_for, render_template, send_file, session, flash
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import pandas as pd
import io
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

app = Flask(__name__)
# ------------Login Credentials-------------
app.secret_key = os.getenv('APP_SECRET_KEY')

# Initialize scheduler after Flask app is created
# Only initialize scheduler if not in debug mode AND not in reloader process
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
            # Double-check the email status to prevent race conditions
            current_client = supabase.table("clientes").select("*").eq("email", cliente['email']).execute()
            if not current_client.data:
                continue

            current_cliente = current_client.data[0]
            data_mergulho = datetime.strptime(current_cliente['data_mergulho'], '%Y-%m-%d').date()
            dias_passados = (hoje - data_mergulho).days

            if dias_passados >= 1 and not current_cliente['primeiro_email_enviado']:
                logger.info(f"SCHEDULED: Sending first email to {current_cliente['email']} (day {dias_passados})")
                if email_feedback(current_cliente, 'primeiro'):
                    supabase.table("clientes").update(
                        {"primeiro_email_enviado": True}
                    ).eq("email", current_cliente['email']).execute()
                    logger.info(f"SCHEDULED: First email sent successfully to {current_cliente['email']}")

            elif dias_passados >= 3 and not current_cliente['segundo_email_enviado']:
                logger.info(f"SCHEDULED: Sending second email to {current_cliente['email']} (day {dias_passados})")
                if email_feedback(current_cliente, 'segundo'):
                    supabase.table("clientes").update(
                        {"segundo_email_enviado": True}
                    ).eq("email", current_cliente['email']).execute()
                    logger.info(f"SCHEDULED: Second email sent successfully to {current_cliente['email']}")

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


# Add a route to check if we're in debug mode
@app.route('/debug-info')
def debug_info():
    """Return debug information"""
    return {
        'debug_mode': app.debug,
        'scheduler_active': scheduler is not None,
        'scheduler_running': scheduler.running if scheduler else False
    }


@app.route('/debug-templates')
def debug_templates():
    """Debug template loading"""
    try:
        import re
        with app.app_context():
            portugues_full = render_template('email_feedback.html', nome="[NOME]")
            ingles_full = render_template('email_feedback_internacional_ingles.html', nome="[NOME]")
            frances_full = render_template('email_feedback_internacional_frances.html', nome="[NOME]")
            alemao_full = render_template('email_feedback_internacional_alemao.html', nome="[NOME]")

            # Extract body content
            portugues_body = re.search(r'<body[^>]*>(.*?)</body>', portugues_full, re.DOTALL | re.IGNORECASE)
            ingles_body = re.search(r'<body[^>]*>(.*?)</body>', ingles_full, re.DOTALL | re.IGNORECASE)
            frances_body = re.search(r'<body[^>]*>(.*?)</body>', frances_full, re.DOTALL | re.IGNORECASE)
            alemao_body = re.search(r'<body[^>]*>(.*?)</body>', alemao_full, re.DOTALL | re.IGNORECASE)

        return {
            'portugues_full_length': len(portugues_full),
            'portugues_body_length': len(portugues_body.group(1).strip()) if portugues_body else 0,
            'ingles_full_length': len(ingles_full),
            'ingles_body_length': len(ingles_body.group(1).strip()) if ingles_body else 0,
            'frances_full_length': len(frances_full),
            'frances_body_length': len(frances_body.group(1).strip()) if frances_body else 0,
            'alemao_full_length': len(alemao_full),
            'alemao_body_length': len(alemao_body.group(1).strip()) if alemao_body else 0,
            'portugues_body_preview': portugues_body.group(1).strip()[:200] + '...' if portugues_body else 'No body found',
            'portugues_full_preview': portugues_full[:200] + '...' if len(portugues_full) > 200 else portugues_full
        }
    except Exception as e:
        return {'error': str(e)}


@app.route('/test-template-content')
def test_template_content():
    """Test what content is being passed to the edit template"""
    try:
        import re
        with app.app_context():
            portugues_full = render_template('email_feedback.html', nome="[NOME]")
            body_match = re.search(r'<body[^>]*>(.*?)</body>', portugues_full, re.DOTALL | re.IGNORECASE)
            portugues_body = body_match.group(1).strip() if body_match else portugues_full
            
            template_content = {'português': portugues_body}
            
            return render_template('test_template.html', template_content=template_content)
    except Exception as e:
        return f'Error: {str(e)}'


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

            supabase.table("clientes").insert({
                "adicionado_por": session.get('username', 'desconhecido'),
                "nome": request.form['nome'],
                "num_mergulho": int(request.form['num_mergulho']),
                "email": email,
                "data_mergulho": request.form['data_mergulho'],
                "valor_fatura": float(request.form['valor_fatura']),
                "desconto": desconto,
                "iva": float(request.form.get('iva', 0.22)),
                "nacionalidade": request.form.get('nacionalidade', 'portugues'),
                "primeiro_email_enviado": False,
                "segundo_email_enviado": False,
                "email_manual_enviado": False,

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

        # Fetch client from Supabase
        response = supabase.table("clientes").select("*").eq("email", email).execute()
        if not response.data:
            return 'Cliente não encontrado', 404

        cliente = response.data[0]

        # Check if email was already sent
        if cliente['email_manual_enviado']:
            logger.info(f'Email já enviado para {email}')
            return 'Email já enviado', 400

        # Send the custom email
        if enviar_email_personalizado_aux(cliente['email'], subject, content):
            # Update in Supabase
            supabase.table("clientes").update({"email_manual_enviado": True}).eq("email", email).execute()
            logger.info(f'Email personalizado enviado com sucesso para {email}')
            return '', 204
        else:
            logger.error(f'Falha ao enviar email personalizado para {email}')
            return 'Falha ao enviar email', 400

    except Exception as e:
        logger.error(f'Erro ao enviar email personalizado: {str(e)}')
        return str(e), 500


def enviar_email_personalizado_aux(destinatario, assunto, conteudo):
    try:
        msg = MIMEMultipart("alternative")
        msg['From'] = app.config['SMTP_USERNAME']
        msg['To'] = destinatario
        msg['Subject'] = assunto
        msg.attach(MIMEText(conteudo, "html"))

        with smtplib.SMTP_SSL(app.config['SMTP_SERVER'], app.config['SMTP_PORT']) as server:
            server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
            server.send_message(msg)
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
            'Valor(€)': cliente["valor_fatura"],
            'Valor com Iva': cliente["valor_fatura"] * 1.22,
            'IVA': cliente["valor_fatura"] * 0.22,
            'Desconto': cliente["desconto"]
        } for cliente in clientes]

        df = pd.DataFrame(clientes_data)
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Clientes')
            workbook = writer.book
            worksheet = writer.sheets['Clientes']

            for row in worksheet.iter_rows(min_row=2, min_col=5, max_col=11):
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


# def open_browser():
#   webbrowser.open_new_tab("http://127.0.0.1:5000")


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
            }
            template_file = template_files.get(nacionalidade, 'email_feedback.html')

            with app.app_context():
                full_template = render_template(template_file, nome="[NOME]")
                # Extract only the body content for the editor
                body_match = re.search(r'<body[^>]*>(.*?)</body>', full_template, re.DOTALL | re.IGNORECASE)
                if body_match:
                    template_content[nacionalidade] = body_match.group(1).strip()
                    logger.info(f"Extracted body content for {nacionalidade}: {len(template_content[nacionalidade])} chars")
                else:
                    # Fallback to full template if no body tag found
                    template_content[nacionalidade] = full_template
                    logger.info(f"Using full template for {nacionalidade}: {len(template_content[nacionalidade])} chars")
            logger.info(f"Loaded template from file for {nacionalidade} ({editing_template})")

        except Exception as e:
            logger.error(f"Error getting template for {nacionalidade}: {str(e)}")
            template_content[
                nacionalidade] = f"<p>Olá [NOME],</p><p>Obrigado pela sua experiência de mergulho!</p><p>Atenciosamente,<br>Atlantic Diving Center</p>"

    # All templates are loaded from files, so they're all "default"
    template_status = {
        'português': 'default',
        'inglês': 'default',
        'francês': 'default',
        'alemão': 'default'
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
                flash('Nenhum destinatário encontrado. Adicione emails ou marque "Incluir clientes da base de dados".', 'warning')
                return redirect(url_for('marketing_emails'))

            # Send marketing email to all recipients
            emails_sent = 0
            failed_emails = []

            for email in all_emails:
                try:
                    # Create the email message
                    msg = MIMEMultipart("alternative")
                    msg['From'] = app.config['SMTP_USERNAME']
                    msg['To'] = email
                    msg['Subject'] = subject
                    msg.attach(MIMEText(content, "html"))

                    # Send the email
                    with smtplib.SMTP_SSL(app.config['SMTP_SERVER'], app.config['SMTP_PORT']) as server:
                        server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
                        server.send_message(msg)

                    emails_sent += 1
                    logger.info(f"Marketing email sent to {email}")

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

    # Load existing emails from file
    saved_emails = ""
    try:
        if os.path.exists(marketing_emails_file):
            with open(marketing_emails_file, 'r', encoding='utf-8') as f:
                saved_emails = f.read()
            logger.info(f"Loaded {len(saved_emails.split())} emails from {marketing_emails_file}")
    except Exception as e:
        logger.error(f"Error loading marketing emails file: {str(e)}")

    # Get client count for display
    response = supabase.table("clientes").select("*").execute()
    client_count = len(response.data)

    return render_template('marketing_emails.html', client_count=client_count, saved_emails=saved_emails)


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


if __name__ == '__main__':
    # Timer(3, open_browser).start()
    app.run(debug=True)