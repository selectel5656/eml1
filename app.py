import os
import base64
import random
import string
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    send_from_directory
)
from werkzeug.utils import secure_filename
from models import db, User, EmailEntry, Macro, Attachment, Proxy, ApiAccount, Setting
from api_client import ApiClient

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data.db'
app.config['SECRET_KEY'] = 'dev'
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)


def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            u = User(username='admin')
            u.set_password('admin')
            db.session.add(u)
        if not Setting.query.filter_by(key='domain').first():
            db.session.add(Setting(key='domain', value='domen.ru'))
        db.session.commit()


init_db()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and user.check_password(request.form['password']):
            session['user_id'] = user.id
            return redirect(url_for('letter'))
        flash('Неверный логин или пароль')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    return redirect(url_for('letter'))


# ------- Letter -------


def get_domain() -> str:
    setting = Setting.query.filter_by(key='domain').first()
    return setting.value if setting else 'domen.ru'

def evaluate_macro(m: Macro) -> str:
    """Evaluate macro value and update usage counters."""
    if m.frequency > 1 and m.usage_count and m.usage_count % m.frequency != 0 and m.current_value:
        m.usage_count += 1
        db.session.commit()
        return m.current_value

    cfg = m.config or {}
    value = ''
    if m.macro_type == 'counter':
        start = int(cfg.get('start', 0))
        step = int(cfg.get('step', 1))
        current = int(cfg.get('current', start))
        value = str(current)
        cfg['current'] = current + step
    elif m.macro_type == 'random':
        chars = cfg.get('chars', string.ascii_letters)
        min_len = int(cfg.get('min_len', 5))
        max_len = int(cfg.get('max_len', 10))
        n = random.randint(min_len, max_len)
        value = ''.join(random.choice(chars) for _ in range(n))
    else:
        value = cfg.get('value', '')
    m.current_value = value
    m.usage_count = 1
    m.config = cfg
    db.session.commit()
    return value


def render_macros(text: str) -> str:
    """Replace macro placeholders with values."""
    macros = Macro.query.all()
    for m in macros:
        value = evaluate_macro(m)
        text = text.replace(f'{{$' + m.name + '}}', value)
    attachments = Attachment.query.all()
    for a in attachments:
        if a.macro_url:
            url = a.remote_url or url_for('uploaded_file', filename=a.filename, _external=True)
            text = text.replace(f'{{$' + a.macro_url + '}}', url)
        if a.macro_base64:
            with open(a.path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            text = text.replace(f'{{$' + a.macro_base64 + '}}', b64)
    return text


@app.route('/letter', methods=['GET', 'POST'])
@login_required
def letter():
    attachments = Attachment.query.all()
    macros = Macro.query.all()
    if request.method == 'POST':
        subject = request.form.get('subject') or ''
        body = request.form.get('body') or ''
        selected = request.form.getlist('attachments')
        body_rendered = render_macros(body)

        account = ApiAccount.query.first()
        if not account:
            flash('Нет API аккаунтов')
            return render_template('letter.html', attachments=attachments, macros=macros)

        client = ApiClient(get_domain(), account.api_key, account.uuid, login=account.login, from_name=account.first_name or '')
        if not client.check_account():
            flash('Аккаунт недоступен')
            return render_template('letter.html', attachments=attachments, macros=macros)

        att_urls = []
        for sid in selected:
            att = Attachment.query.get(int(sid))
            if att:
                if not att.remote_url:
                    res = client.upload_attachment(att.path, att.filename)
                    att.remote_id = res.get('id')
                    att.remote_url = res.get('url')
                    db.session.commit()
                if att.remote_url:
                    att_urls.append(att.remote_url)

        operation_id = client.generate_operation_id()
        recipients = [e.email for e in EmailEntry.query.limit(1).all()]
        if client.send_mail(subject, body_rendered, recipients, att_urls, operation_id):
            flash('Письмо отправлено через API')
        else:
            flash('Ошибка отправки письма')
    return render_template('letter.html', attachments=attachments, macros=macros)


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ------- Email Base -------
import re

@app.route('/email_base', methods=['GET', 'POST'])
@login_required
def email_base():
    if request.method == 'POST':
        fmt = request.form.get('format')
        file = request.files.get('file')
        if file:
            lines = file.read().decode('utf-8').splitlines()
            for line in lines:
                line = line.strip().strip(';')
                if not line:
                    continue
                name = ''
                email = ''
                m = re.match(r'(.*)<([^>]+)>', line)
                if m:
                    name = m.group(1).strip().strip('"')
                    email = m.group(2).strip()
                else:
                    email = line
                if not name:
                    name = email.split('@')[0]
                if not EmailEntry.query.filter_by(email=email).first():
                    db.session.add(EmailEntry(name=name, email=email))
            db.session.commit()
            flash('База загружена')
    emails = EmailEntry.query.limit(50).all()
    count = EmailEntry.query.count()
    return render_template('email_base.html', emails=emails, count=count)


# ------- Macros -------
@app.route('/macros', methods=['GET', 'POST'])
@login_required
def macros():
    if request.method == 'POST':
        name = request.form.get('name')
        macro_type = request.form.get('macro_type')
        frequency = int(request.form.get('frequency') or 1)
        cfg = {}
        if macro_type == 'counter':
            cfg = {
                'start': int(request.form.get('start') or 0),
                'step': int(request.form.get('step') or 1),
                'current': int(request.form.get('start') or 0)
            }
        elif macro_type == 'random':
            cfg = {
                'chars': request.form.get('chars') or string.ascii_letters,
                'min_len': int(request.form.get('min_len') or 5),
                'max_len': int(request.form.get('max_len') or 10)
            }
        if name:
            db.session.add(Macro(name=name, macro_type=macro_type, config=cfg, frequency=frequency))
            db.session.commit()
            flash('Макрос добавлен')
    macros = Macro.query.all()
    return render_template('macros.html', macros=macros)


# ------- Attachments -------
@app.route('/attachments', methods=['GET', 'POST'])
@login_required
def attachments():
    if request.method == 'POST':
        file = request.files.get('file')
        display_name = request.form.get('display_name') or (file.filename if file else '')
        inline = bool(request.form.get('inline'))
        if file and file.filename:
            filename = secure_filename(file.filename)
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(path)
            attach = Attachment(display_name=display_name, filename=filename, path=path, inline=inline)
            db.session.add(attach)
            db.session.commit()
            attach.macro_url = f'url_attach_{attach.id}'
            if inline and filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                attach.macro_base64 = f'attach_img_{attach.id}_base64'
            db.session.commit()
            flash('Вложение добавлено')
    attachments = Attachment.query.all()
    return render_template('attachments.html', attachments=attachments)


# ------- Proxies -------
@app.route('/proxies', methods=['GET', 'POST'])
@login_required
def proxies():
    if request.method == 'POST':
        file = request.files.get('file')
        if file:
            lines = file.read().decode().splitlines()
            for line in lines:
                addr = line.strip()
                if addr and not Proxy.query.filter_by(address=addr).first():
                    db.session.add(Proxy(address=addr))
            db.session.commit()
            flash('Прокси загружены')
    proxies = Proxy.query.all()
    return render_template('proxies.html', proxies=proxies)


# ------- API Accounts -------
@app.route('/api_accounts', methods=['GET', 'POST'])
@login_required
def api_accounts():
    if request.method == 'POST':
        file = request.files.get('file')
        if file:
            lines = file.read().decode().splitlines()
            for line in lines:
                parts = line.strip().split(':')
                if len(parts) >= 6:
                    login, password, first_name, last_name, api_key, uuid = parts[:6]
                    if not ApiAccount.query.filter_by(login=login).first():
                        db.session.add(ApiAccount(login=login, password=password,
                                                   first_name=first_name, last_name=last_name,
                                                   api_key=api_key, uuid=uuid))
            db.session.commit()
            flash('Аккаунты загружены')
    accounts = ApiAccount.query.all()
    return render_template('api_accounts.html', accounts=accounts)


# ------- Settings -------
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    domain_setting = Setting.query.filter_by(key='domain').first()
    if request.method == 'POST':
        domain = request.form.get('domain')
        password = request.form.get('password')
        if domain:
            domain_setting.value = domain
        if password:
            user = User.query.filter_by(username='admin').first()
            user.set_password(password)
        db.session.commit()
        flash('Настройки сохранены')
    domain = domain_setting.value if domain_setting else ''
    return render_template('settings.html', domain=domain)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
