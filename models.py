from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# Initialize SQLAlchemy (to be bound in app)
db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class EmailEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))
    email = db.Column(db.String(200), unique=True, nullable=False)

class Macro(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    macro_type = db.Column(db.String(50), nullable=False)
    config = db.Column(db.JSON, nullable=False)
    frequency = db.Column(db.Integer, default=1)
    usage_count = db.Column(db.Integer, default=0)
    current_value = db.Column(db.String(500))

class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(200))
    filename = db.Column(db.String(200))
    path = db.Column(db.String(500))
    inline = db.Column(db.Boolean, default=False)
    upload_to_server = db.Column(db.Boolean, default=True)
    macro_base64 = db.Column(db.String(100))
    macro_url = db.Column(db.String(100))
    macro_id = db.Column(db.String(100))
    remote_id = db.Column(db.String(500))
    remote_url = db.Column(db.String(500))

class Proxy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(200), unique=True, nullable=False)
    in_use = db.Column(db.Boolean, default=False)

class ApiAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(200), unique=True, nullable=False)
    password = db.Column(db.String(200))
    first_name = db.Column(db.String(200))
    last_name = db.Column(db.String(200))
    api_key = db.Column(db.String(500))
    uuid = db.Column(db.String(200))
    send_count = db.Column(db.Integer, default=0)
    proxy_id = db.Column(db.Integer, db.ForeignKey('proxy.id'))
    proxy = db.relationship('Proxy')

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(200), unique=True, nullable=False)
    value = db.Column(db.String(500))
