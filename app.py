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
import time
from functools import wraps

#--------Load Environment Variables-------
load_dotenv()

#--------Initialize Scheduler-------------
scheduler = BackgroundScheduler(daemon=True)

scheduler.start()

atexit.register(lambda: scheduler.shutdown())

#--------Initialize Supabase-------
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

#--------Configure Logging---------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


#------------Login Credentials-------------
username = os.getenv('APP_SECRET_USERNAME')
password = os.getenv('APP_SECRET_PASSWORD')
app.secret_key = os.getenv('APP_SECRET_KEY')

#--------Email Configuration------------
app.config['SMTP_SERVER'] = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
app.config['SMTP_PORT'] = int(os.getenv('SMTP_PORT', 465))
app.config['SMTP_USERNAME'] = os.getenv('SMTP_USERNAME')
app.config['SMTP_PASSWORD'] = os.getenv('SMTP_PASSWORD')

#---------Login/Logout Functions--------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- Email Functions ---
def enviar_email(destinatario, assunto, nome, internacional):
    try:
        template = {
            'português': 'email_feedback.html',
            'inglês': 'email_feedback_internacional_ingles.html',
            'alemão': 'email_feedback_internacional_alemao.html',
            'francês': 'email_feedback_internacional_frances.html',
        }
        with app.app_context():
            corpo_html = render_template(template[internacional], nome=nome)
            msg = MIMEMultipart("alternative")
            msg['From'] = app.config['SMTP_USERNAME']
            msg['To'] = destinatario
            msg['Subject'] = assunto
            msg.attach(MIMEText(corpo_html, "html"))

            with smtplib.SMTP_SSL(app.config['SMTP_SERVER'], app.config['SMTP_PORT']) as server:
                server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
                server.send_message(msg)
            return True
    except Exception as e:
        logger.error(f"Email failed: {str(e)}")
        return False


def email_feedback(cliente):
    assunto = {
        'inglês': "Thank you for your diving experience!",
        'francês': "Merci d'avoir plongé avec nous",
        'alemão': "Danke für Ihr Taucherlebnis",
    }.get(cliente['nacionalidade'], "Obrigado pela sua experiência de mergulho!")
    return enviar_email(cliente['email'], assunto, cliente['nome'], cliente['nacionalidade'])


#----------Request-Based Email Checker-----------
def check_and_send_emails():
    with app.app_context():  # Ensure Flask context
        hoje = datetime.now().date()
        response = supabase.table("clientes").select("*").execute()
        clientes = response.data

        for cliente in clientes:
            data_mergulho = datetime.strptime(cliente['data_mergulho'], '%Y-%m-%d').date()
            dias_passados = (hoje - data_mergulho).days

            if dias_passados == 1 and not cliente['primeiro_email_enviado']:
                if email_feedback(cliente):
                    supabase.table("clientes").update(
                        {"primeiro_email_enviado": True}
                    ).eq("email", cliente['email']).execute()

            elif dias_passados == 3 and not cliente['segundo_email_enviado']:
                if email_feedback(cliente):
                    supabase.table("clientes").update(
                        {"segundo_email_enviado": True}
                    ).eq("email", cliente['email']).execute()

#------Email Sending Scheduler-------
scheduler.add_job(
    check_and_send_emails,
    'interval',
    minutes=1,
    timezone="Europe/Lisbon"
)

#---------Time-Checker----------
@app.before_request
def handle_scheduled_tasks():
    """Runs before each request to check emails"""
    if not hasattr(app, 'last_email_check'):
        app.last_email_check = datetime.now()

    # Check emails every minute (adjust as needed)
    if datetime.now() - app.last_email_check > timedelta(minutes=1):
        check_and_send_emails()
        app.last_email_check = datetime.now()


#------------Flask Routes-----------
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
            supabase.table("clientes").insert({
                "nome": request.form['nome'],
                "num_mergulho": int(request.form['num_mergulho']),
                "email": email,
                "data_mergulho": request.form['data_mergulho'],
                "valor_fatura": float(request.form['valor_fatura']),
                "nacionalidade": request.form.get('nacionalidade', 'portugues'),
                "primeiro_email_enviado": False,
                "segundo_email_enviado": False,
                "email_manual_enviado": False
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


#--------Send Email Manually---------
@app.route('/enviar/<email>', methods=['POST'])
def enviar_manual(email):
    try:
        # Fetch client from Supabase
        response = supabase.table("clientes").select("*").eq("email", email).execute()
        if not response.data:
            return 'Cliente não encontrado', 404

        cliente = response.data[0]

        if email_feedback(cliente):
            # Update in Supabase
            supabase.table("clientes").update({"email_manual_enviado": True}).eq("email", email).execute()

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return '', 204
            logger.info('Email enviado com sucesso!')
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return 'Falha ao enviar email', 400
            logger.info('Falha ao enviar email')

    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return str(e), 500
        logger.info(f'Erro: {str(e)}')

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return '', 204
    return redirect(url_for('index'))


#---------Remover---------
@app.route('/remover/<email>', methods=['POST'])
def remover_cliente(email):
    try:
        # Delete from Supabase
        supabase.table("clientes").delete().eq("email", email).execute()
        return '', 204  # Successful deletion returns no content
    except Exception as e:
        return str(e), 500


#-------Send Email to All-----------
@app.route('/enviar-todos', methods=['POST'])
def enviar_manual_todos():
    try:
        # Fetch all clients from Supabase
        response = supabase.table("clientes").select("*").execute()
        clientes = response.data

        for cliente in clientes:
            if email_feedback(cliente):
                supabase.table("clientes").update({"email_manual_enviado": True}).eq("email",
                                                                                     cliente["email"]).execute()
                logger.info(f'Email enviado com sucesso para {cliente["email"]}')
            else:
                logger.error(f'Falha ao enviar email para {cliente["email"]}')

        logger.info('Emails enviados para todos os clientes')
        return redirect(url_for('index'))

    except Exception as e:
        logger.error(f'Erro ao enviar emails: {str(e)}')
        return redirect(url_for('index'))

#--------Debug----------
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


#--------Table Refreshing------------
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


#--------Send Email to All------------
@app.route('/exportar-emails')
def exportar_emails():
    try:
        response = supabase.table("clientes").select("*").execute()
        clientes = response.data

        clientes_data = [{
            'Nome': cliente["nome"],
            'Email': cliente["email"],
            'Nº Mergulhos': cliente["num_mergulho"],
            'Data Mergulho': datetime.strptime(cliente["data_mergulho"], "%Y-%m-%d").strftime('%Y/%m/%d'),
            'Valor da fatura(€)': cliente["valor_fatura"],
            'Nacionalidade': cliente["nacionalidade"].capitalize(),
            '1º Email Enviado': 'Sim' if cliente["primeiro_email_enviado"] else 'Não',
            '2º Email Enviado': 'Sim' if cliente["segundo_email_enviado"] else 'Não',
            'Email Manual': 'Sim' if cliente["email_manual_enviado"] else 'Não'
        } for cliente in clientes]

        df = pd.DataFrame(clientes_data)
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Clientes')
            workbook = writer.book
            worksheet = writer.sheets['Clientes']

            for row in worksheet.iter_rows(min_row=2, min_col=5, max_col=5):
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
                adjusted_width = (max_length + 2)
                worksheet.column_dimensions[column_letter].width = adjusted_width

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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        entered_username = request.form['username']
        entered_password = request.form['password']
        if entered_username == username and entered_password == password:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials')
    return render_template('login.html')

def open_browser():
    webbrowser.open_new_tab("http://127.0.0.1:5000")


if __name__ == '__main__':
    Timer(3, open_browser).start()
    app.run(debug=True)