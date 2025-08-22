import os
import base64
import random
import string
import time
import requests
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
        if not Setting.query.filter_by(key='user_agent').first():
            db.session.add(Setting(key='user_agent', value=ApiClient.USER_AGENT))
        if not Setting.query.filter_by(key='per_account_limit').first():
            db.session.add(Setting(key='per_account_limit', value='1'))
        if not Setting.query.filter_by(key='cycle_accounts').first():
            db.session.add(Setting(key='cycle_accounts', value='no'))
        if not Setting.query.filter_by(key='send_attempts').first():
            db.session.add(Setting(key='send_attempts', value='1'))
        if not Setting.query.filter_by(key='server_timeout').first():
            db.session.add(Setting(key='server_timeout', value='30'))
        if not Setting.query.filter_by(key='pause_between').first():
            db.session.add(Setting(key='pause_between', value='0'))
        if not Setting.query.filter_by(key='recipients_per_message').first():
            db.session.add(Setting(key='recipients_per_message', value='1'))
        if not Setting.query.filter_by(key='recipient_method').first():
            db.session.add(Setting(key='recipient_method', value='bcc'))
        if not Setting.query.filter_by(key='first_recipient_to').first():
            db.session.add(Setting(key='first_recipient_to', value='no'))
        if not Setting.query.filter_by(key='quality_every').first():
            db.session.add(Setting(key='quality_every', value='0'))
        if not Setting.query.filter_by(key='quality_email').first():
            db.session.add(Setting(key='quality_email', value=''))
        if not Setting.query.filter_by(key='total_sent').first():
            db.session.add(Setting(key='total_sent', value='0'))
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


def get_setting(key: str, default: str = '') -> str:
    setting = Setting.query.filter_by(key=key).first()
    return setting.value if setting else default


def get_domain() -> str:
    return get_setting('domain', 'domen.ru')


def get_user_agent() -> str:
    return get_setting('user_agent', ApiClient.USER_AGENT)


def check_proxy(address: str) -> bool:
    try:
        proxies = {'http': f'http://{address}', 'https': f'http://{address}'}
        r = requests.get('https://httpbin.org/ip', proxies=proxies, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def acquire_proxy() -> str | None:
    for proxy in Proxy.query.filter_by(in_use=False).all():
        if check_proxy(proxy.address):
            proxy.in_use = True
            db.session.commit()
            return proxy.address
    Proxy.query.update({Proxy.in_use: False})
    db.session.commit()
    return None

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

        limit = int(get_setting('per_account_limit', '1'))
        cycle = get_setting('cycle_accounts', 'no') == 'yes'
        attempts = int(get_setting('send_attempts', '1'))
        timeout = int(get_setting('server_timeout', '30'))
        pause = int(get_setting('pause_between', '0'))
        rec_count = int(get_setting('recipients_per_message', '1'))
        method = get_setting('recipient_method', 'bcc')
        first = get_setting('first_recipient_to', 'no') == 'yes'
        q_every = int(get_setting('quality_every', '0'))
        q_email = get_setting('quality_email', '')

        account = ApiAccount.query.filter(ApiAccount.send_count < limit).order_by(ApiAccount.id).first()
        if not account and cycle:
            ApiAccount.query.update({ApiAccount.send_count: 0})
            db.session.commit()
            account = ApiAccount.query.filter(ApiAccount.send_count < limit).order_by(ApiAccount.id).first()
        if not account:
            flash('Нет доступных API аккаунтов')
            return render_template('letter.html', attachments=attachments, macros=macros)

        proxy_addr = acquire_proxy()
        client = ApiClient(
            get_domain(),
            account.api_key,
            account.uuid,
            login=account.login,
            from_name=account.first_name or '',
            user_agent=get_user_agent(),
            proxy=proxy_addr,
            timeout=timeout,
        )
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
        recipients = [e.email for e in EmailEntry.query.limit(rec_count).all()]
        if not recipients:
            flash('Нет получателей')
            return render_template('letter.html', attachments=attachments, macros=macros)
        success = False
        for _ in range(attempts):
            if client.send_mail(
                subject,
                body_rendered,
                recipients,
                att_urls,
                operation_id,
                method=method,
                first_to=first,
            ):
                success = True
                break
        if success:
            account.send_count = account.send_count + 1
            total_sent = int(get_setting('total_sent', '0')) + 1
            Setting.query.filter_by(key='total_sent').first().value = str(total_sent)
            db.session.commit()
            if q_every and q_email and total_sent % q_every == 0:
                client.send_mail(
                    'Test',
                    'Quality check',
                    [q_email],
                    [],
                    client.generate_operation_id(),
                    method='to',
                )
            flash('Письмо отправлено через API')
            if pause > 0:
                time.sleep(pause)
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


@app.route('/email_base/delete/<int:email_id>')
@login_required
def delete_email(email_id):
    entry = EmailEntry.query.get_or_404(email_id)
    db.session.delete(entry)
    db.session.commit()
    flash('Адрес удален')
    return redirect(url_for('email_base'))


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


@app.route('/macros/delete/<int:macro_id>')
@login_required
def delete_macro(macro_id):
    macro = Macro.query.get_or_404(macro_id)
    db.session.delete(macro)
    db.session.commit()
    flash('Макрос удален')
    return redirect(url_for('macros'))


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


@app.route('/attachments/delete/<int:att_id>')
@login_required
def delete_attachment(att_id):
    att = Attachment.query.get_or_404(att_id)
    try:
        if att.path and os.path.exists(att.path):
            os.remove(att.path)
    except Exception:
        pass
    db.session.delete(att)
    db.session.commit()
    flash('Вложение удалено')
    return redirect(url_for('attachments'))


# ------- API Rules -------
@app.route('/api_rules', methods=['GET', 'POST'])
@login_required
def api_rules():
    limit = get_setting('per_account_limit', '1')
    cycle = get_setting('cycle_accounts', 'no')
    attempts = get_setting('send_attempts', '1')
    timeout = get_setting('server_timeout', '30')
    pause = get_setting('pause_between', '0')
    recipients = get_setting('recipients_per_message', '1')
    method = get_setting('recipient_method', 'bcc')
    first = get_setting('first_recipient_to', 'no')
    q_every = get_setting('quality_every', '0')
    q_email = get_setting('quality_email', '')
    if request.method == 'POST':
        limit = request.form.get('per_account_limit') or '1'
        cycle = 'yes' if request.form.get('cycle_accounts') else 'no'
        attempts = request.form.get('send_attempts') or '1'
        timeout = request.form.get('server_timeout') or '30'
        pause = request.form.get('pause_between') or '0'
        recipients = request.form.get('recipients_per_message') or '1'
        method = request.form.get('recipient_method') or 'bcc'
        first = 'yes' if request.form.get('first_recipient_to') else 'no'
        q_every = request.form.get('quality_every') or '0'
        q_email = request.form.get('quality_email') or ''
        Setting.query.filter_by(key='per_account_limit').first().value = limit
        Setting.query.filter_by(key='cycle_accounts').first().value = cycle
        Setting.query.filter_by(key='send_attempts').first().value = attempts
        Setting.query.filter_by(key='server_timeout').first().value = timeout
        Setting.query.filter_by(key='pause_between').first().value = pause
        Setting.query.filter_by(key='recipients_per_message').first().value = recipients
        Setting.query.filter_by(key='recipient_method').first().value = method
        Setting.query.filter_by(key='first_recipient_to').first().value = first
        Setting.query.filter_by(key='quality_every').first().value = q_every
        Setting.query.filter_by(key='quality_email').first().value = q_email
        db.session.commit()
        flash('Правила сохранены')
    return render_template(
        'api_rules.html',
        limit=limit,
        cycle=cycle == 'yes',
        attempts=attempts,
        timeout=timeout,
        pause=pause,
        recipients=recipients,
        method=method,
        first=first == 'yes',
        q_every=q_every,
        q_email=q_email,
    )


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


@app.route('/proxies/delete/<int:proxy_id>')
@login_required
def delete_proxy(proxy_id):
    proxy = Proxy.query.get_or_404(proxy_id)
    db.session.delete(proxy)
    db.session.commit()
    flash('Прокси удален')
    return redirect(url_for('proxies'))


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


@app.route('/api_accounts/delete/<int:acc_id>')
@login_required
def delete_api_account(acc_id):
    acc = ApiAccount.query.get_or_404(acc_id)
    db.session.delete(acc)
    db.session.commit()
    flash('Аккаунт удален')
    return redirect(url_for('api_accounts'))


@app.route('/api_accounts/reset_counts')
@login_required
def reset_counts():
    ApiAccount.query.update({ApiAccount.send_count: 0})
    db.session.commit()
    flash('Счетчики сброшены')
    return redirect(url_for('api_accounts'))


# ------- Settings -------
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    domain_setting = Setting.query.filter_by(key='domain').first()
    ua_setting = Setting.query.filter_by(key='user_agent').first()
    if request.method == 'POST':
        domain = request.form.get('domain')
        user_agent = request.form.get('user_agent')
        password = request.form.get('password')
        if domain:
            domain_setting.value = domain
        if user_agent and ua_setting:
            ua_setting.value = user_agent
        if password:
            user = User.query.filter_by(username='admin').first()
            user.set_password(password)
        db.session.commit()
        flash('Настройки сохранены')
    domain = domain_setting.value if domain_setting else ''
    user_agent = ua_setting.value if ua_setting else ''
    return render_template('settings.html', domain=domain, user_agent=user_agent)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
