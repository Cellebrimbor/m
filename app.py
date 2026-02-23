import os111
import zipfile
import shutil
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from datetime import datetime, timedelta
import bcrypt
import re
import traceback
import uuid
from werkzeug.utils import secure_filename
from pathlib import Path

# Создаем приложение
app = Flask(__name__)

# Конфигурация
app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///messenger.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'jwt-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

# URL сервера с контентом (второй сервер)
app.config['CONTENT_SERVER_URL'] = 'http://files.vasamuseum.shop'  # ✅ Локальный сервер контента  # Замените на реальный URL второго сервера

# Инициализация расширений
db = SQLAlchemy(app)
jwt = JWTManager(app)

# Создаем папки
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'temp'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'avatars'), exist_ok=True)

# ==================== МОДЕЛИ ДАННЫХ ====================

# Модель пользователя
class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(80), nullable=False, default='')
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    is_online = db.Column(db.Boolean, default=False)
    selected_emoji_id = db.Column(db.Integer, db.ForeignKey('emojis.id'), nullable=True)
    avatar_url = db.Column(db.String(500))  # Для загруженных аватарок
    
    # Отношения
    selected_emoji = db.relationship('Emoji', foreign_keys=[selected_emoji_id])
    
    def set_password(self, password):
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'display_name': self.display_name or self.username,
            'email': self.email,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'is_online': self.is_online,
            'selected_emoji': self.selected_emoji.to_dict() if self.selected_emoji else None,
            'avatar_url': self.avatar_url
        }

# Модель стикер-пака (метаданные, ссылки на второй сервер)
class StickerPack(db.Model):
    __tablename__ = 'sticker_packs'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True)  # ID на втором сервере
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    icon_path = db.Column(db.String(500))  # Путь на втором сервере
    preview_path = db.Column(db.String(500))  # Путь превью
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_default = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    stickers_count = db.Column(db.Integer, default=0)
    
    # Отношения
    user_packs = db.relationship('UserStickerPack', backref='pack', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'external_id': self.external_id,
            'name': self.name,
            'description': self.description,
            'icon_url': f"{app.config['CONTENT_SERVER_URL']}{self.icon_path}" if self.icon_path else None,
            'preview_url': f"{app.config['CONTENT_SERVER_URL']}{self.preview_path}" if self.preview_path else None,
            'stickers_count': self.stickers_count,
            'is_default': self.is_default
        }

# Модель стикера (метаданные, ссылки на второй сервер)
class Sticker(db.Model):
    __tablename__ = 'stickers'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True)
    pack_id = db.Column(db.Integer, db.ForeignKey('sticker_packs.id'), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    image_path = db.Column(db.String(500))  # Путь на втором сервере
    emoji = db.Column(db.String(10))
    order_index = db.Column(db.Integer, default=0)
    
    pack = db.relationship('StickerPack', backref='stickers')
    
    def to_dict(self):
        return {
            'id': self.id,
            'external_id': self.external_id,
            'pack_id': self.pack_id,
            'file_name': self.file_name,
            'image_url': f"{app.config['CONTENT_SERVER_URL']}{self.image_path}" if self.image_path else None,
            'emoji': self.emoji,
            'order_index': self.order_index
        }

# Модель эмодзи-пака (метаданные, ссылки на второй сервер)
class EmojiPack(db.Model):
    __tablename__ = 'emoji_packs'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    icon_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_default = db.Column(db.Boolean, default=False)
    emojis_count = db.Column(db.Integer, default=0)
    
    emojis = db.relationship('Emoji', backref='pack', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'external_id': self.external_id,
            'name': self.name,
            'description': self.description,
            'icon_url': f"{app.config['CONTENT_SERVER_URL']}{self.icon_path}" if self.icon_path else None,
            'emojis_count': self.emojis_count,
            'is_default': self.is_default
        }

# Модель эмодзи (метаданные, ссылки на второй сервер)
class Emoji(db.Model):
    __tablename__ = 'emojis'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True)
    pack_id = db.Column(db.Integer, db.ForeignKey('emoji_packs.id'), nullable=False)
    character = db.Column(db.String(10))  # Сам символ эмодзи
    image_path = db.Column(db.String(500))  # Путь на втором сервере
    name = db.Column(db.String(100))  # Название эмодзи
    order_index = db.Column(db.Integer, default=0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'external_id': self.external_id,
            'pack_id': self.pack_id,
            'character': self.character,
            'image_url': f"{app.config['CONTENT_SERVER_URL']}{self.image_path}" if self.image_path else None,
            'name': self.name,
            'order_index': self.order_index
        }

# Модель связи пользователя со стикер-паками
class UserStickerPack(db.Model):
    __tablename__ = 'user_sticker_packs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pack_id = db.Column(db.Integer, db.ForeignKey('sticker_packs.id'), nullable=False)
    downloaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_favorite = db.Column(db.Boolean, default=False)
    
    __table_args__ = (db.UniqueConstraint('user_id', 'pack_id', name='unique_user_sticker_pack'),)

# Модель связи пользователя с эмодзи-паками
class UserEmojiPack(db.Model):
    __tablename__ = 'user_emoji_packs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pack_id = db.Column(db.Integer, db.ForeignKey('emoji_packs.id'), nullable=False)
    downloaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_favorite = db.Column(db.Boolean, default=False)
    
    __table_args__ = (db.UniqueConstraint('user_id', 'pack_id', name='unique_user_emoji_pack'),)

# Модель Chat
class Chat(db.Model):
    __tablename__ = 'chats'
    
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    messages = db.relationship('Message', backref='chat', lazy=True, cascade='all, delete-orphan')
    
    __table_args__ = (db.UniqueConstraint('user1_id', 'user2_id', name='unique_chat'),)
    
    def to_dict(self, current_user_id=None):
        other_user = User.query.get(self.user2_id if self.user1_id == current_user_id else self.user1_id)
        last_message = Message.query.filter_by(chat_id=self.id).order_by(Message.created_at.desc()).first()
        
        unread_count = 0
        if current_user_id:
            unread_count = Message.query.filter_by(
                chat_id=self.id, 
                is_read=False
            ).filter(Message.sender_id != current_user_id).count()
        
        return {
            'id': self.id,
            'with_user': other_user.to_dict() if other_user else None,
            'last_message': last_message.content if last_message else None,
            'last_message_time': last_message.created_at.isoformat() if last_message else None,
            'unread_count': unread_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

# Модель Message
class Message(db.Model):
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=True)
    sticker_id = db.Column(db.Integer, db.ForeignKey('stickers.id'), nullable=True)
    emoji_id = db.Column(db.Integer, db.ForeignKey('emojis.id'), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    message_type = db.Column(db.String(20), default='text')  # text, sticker, emoji
    
    sticker = db.relationship('Sticker', foreign_keys=[sticker_id])
    emoji = db.relationship('Emoji', foreign_keys=[emoji_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'chat_id': self.chat_id,
            'sender_id': self.sender_id,
            'receiver_id': self.receiver_id,
            'content': self.content,
            'sticker': self.sticker.to_dict() if self.sticker else None,
            'emoji': self.emoji.to_dict() if self.emoji else None,
            'message_type': self.message_type,
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# Создание таблиц
with app.app_context():
    db.create_all()
    print("✅ База данных инициализирована!")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def is_valid_username(username):
    pattern = r'^[a-zA-Z0-9_]{3,30}$'
    return re.match(pattern, username) is not None

def sync_with_content_server():
    """Синхронизирует метаданные со вторым сервером контента"""
    try:
        print("🔄 Синхронизация с сервером контента...")
        
        # Запрос к второму серверу для получения списка паков
        response = requests.get(f"{app.config['CONTENT_SERVER_URL']}/api/content/packs")
        
        if response.status_code == 200:
            data = response.json()
            
            # Синхронизация стикер-паков
            for pack_data in data.get('sticker_packs', []):
                pack = StickerPack.query.filter_by(external_id=pack_data['external_id']).first()
                if not pack:
                    pack = StickerPack(
                        external_id=pack_data['external_id'],
                        name=pack_data['name'],
                        description=pack_data['description'],
                        icon_path=pack_data['icon_path'],
                        preview_path=pack_data.get('preview_path'),
                        stickers_count=pack_data['stickers_count'],
                        is_default=pack_data.get('is_default', False)
                    )
                    db.session.add(pack)
                    db.session.flush()
                    
                    # Добавляем стикеры
                    for sticker_data in pack_data['stickers']:
                        sticker = Sticker(
                            external_id=sticker_data['external_id'],
                            pack_id=pack.id,
                            file_name=sticker_data['file_name'],
                            image_path=sticker_data['image_path'],
                            emoji=sticker_data.get('emoji'),
                            order_index=sticker_data.get('order_index', 0)
                        )
                        db.session.add(sticker)
            
            # Синхронизация эмодзи-паков
            for pack_data in data.get('emoji_packs', []):
                pack = EmojiPack.query.filter_by(external_id=pack_data['external_id']).first()
                if not pack:
                    pack = EmojiPack(
                        external_id=pack_data['external_id'],
                        name=pack_data['name'],
                        description=pack_data['description'],
                        icon_path=pack_data['icon_path'],
                        emojis_count=pack_data['emojis_count'],
                        is_default=pack_data.get('is_default', False)
                    )
                    db.session.add(pack)
                    db.session.flush()
                    
                    # Добавляем эмодзи
                    for emoji_data in pack_data['emojis']:
                        emoji = Emoji(
                            external_id=emoji_data['external_id'],
                            pack_id=pack.id,
                            character=emoji_data.get('character'),
                            image_path=emoji_data.get('image_path'),
                            name=emoji_data['name'],
                            order_index=emoji_data.get('order_index', 0)
                        )
                        db.session.add(emoji)
            
            db.session.commit()
            print("✅ Синхронизация с сервером контента завершена")
        else:
            print(f"❌ Ошибка при запросе к серверу контента: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("❌ Не удалось подключиться к серверу контента")
    except Exception as e:
        print(f"❌ Ошибка синхронизации: {str(e)}")
        db.session.rollback()

# ==================== БАЗОВЫЕ ЭНДПОИНТЫ ====================

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'message': 'Server is running'}), 200

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided. Expected JSON with username, email, and password'}), 400
        
        missing_fields = []
        if not data.get('username'):
            missing_fields.append('username')
        if not data.get('email'):
            missing_fields.append('email')
        if not data.get('password'):
            missing_fields.append('password')
        
        if missing_fields:
            return jsonify({
                'error': f'Missing required fields: {", ".join(missing_fields)}',
                'received_data': {k: v for k, v in data.items() if k != 'password'}
            }), 400
        
        username = data['username'].strip()
        email = data['email'].strip().lower()
        password = data['password']
        display_name = data.get('display_name', '').strip() or username
        
        if not is_valid_username(username):
            return jsonify({'error': 'Invalid username. Use 3-30 characters, letters, numbers and underscore only'}), 400
        
        if not is_valid_email(email):
            return jsonify({'error': 'Invalid email format'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters long'}), 400
        
        if User.query.filter_by(username=username).first():
            return jsonify({'error': 'Username already exists'}), 409
        
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Email already exists'}), 409
        
        new_user = User(
            username=username, 
            email=email, 
            display_name=display_name
        )
        new_user.set_password(password)
        
        db.session.add(new_user)
        db.session.commit()
        
        access_token = create_access_token(
            identity=str(new_user.id),
            expires_delta=timedelta(days=7)
        )
        
        return jsonify({
            'message': 'User registered successfully',
            'user': new_user.to_dict(),
            'access_token': access_token
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"Registration error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': f'Registration failed: {str(e)}'}), 500

@app.route('/api/profile/avatar', methods=['POST'])
@jwt_required()
def upload_avatar():
    """Загрузка аватара пользователя"""
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        if 'avatar' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['avatar']
        
        if file.filename == '':
            return jsonify({'error': 'Empty filename'}), 400
        
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
            return jsonify({'error': 'Invalid file type. Allowed: png, jpg, jpeg, gif, webp'}), 400
        
        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[1].lower()
        new_filename = f"avatar_{user_id}_{uuid.uuid4().hex}.{ext}"
        
        avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], 'avatars', new_filename)
        file.save(avatar_path)
        
        if user.avatar_url:
            old_avatar = os.path.join(app.config['UPLOAD_FOLDER'], user.avatar_url.lstrip('/'))
            if os.path.exists(old_avatar):
                try:
                    os.remove(old_avatar)
                except:
                    pass
        
        avatar_url = f"/uploads/avatars/{new_filename}"
        user.avatar_url = avatar_url
        db.session.commit()
        
        return jsonify({
            'message': 'Avatar uploaded successfully',
            'avatar_url': avatar_url
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Avatar upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/uploads/avatars/<filename>')
def get_avatar(filename):
    """Получение файла аватара"""
    return send_from_directory(
        os.path.join(app.config['UPLOAD_FOLDER'], 'avatars'),
        filename
    )

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        
        if not data or not data.get('login') or not data.get('password'):
            return jsonify({'error': 'Missing login or password'}), 400
        
        login = data['login'].strip()
        password = data['password']
        
        user = User.query.filter(
            (User.username == login) | (User.email == login.lower())
        ).first()
        
        if not user or not user.check_password(password):
            return jsonify({'error': 'Invalid credentials'}), 401
        
        user.is_online = True
        user.last_seen = datetime.utcnow()
        db.session.commit()
        
        access_token = create_access_token(
            identity=str(user.id),
            expires_delta=timedelta(days=7)
        )
        
        return jsonify({
            'message': 'Login successful',
            'user': user.to_dict(),
            'access_token': access_token
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile', methods=['GET'])
@jwt_required()
def get_profile():
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        return jsonify({'user': user.to_dict()}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
@jwt_required()
def logout():
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        
        if user:
            user.is_online = False
            user.last_seen = datetime.utcnow()
            db.session.commit()
        
        return jsonify({'message': 'Logout successful'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/users/search', methods=['GET'])
@jwt_required()
def search_users():
    try:
        query = request.args.get('q', '')
        current_user_id = int(get_jwt_identity())
        
        if len(query) < 2:
            return jsonify({'error': 'Search query must be at least 2 characters'}), 400
        
        users = User.query.filter(
            (User.username.ilike(f'%{query}%') | User.display_name.ilike(f'%{query}%')),
            User.id != current_user_id
        ).limit(20).all()
        
        return jsonify({
            'users': [user.to_dict() for user in users]
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== ЭНДПОИНТЫ ПРОФИЛЯ ====================

@app.route('/api/profile/display_name', methods=['PUT'])
@jwt_required()
def update_display_name():
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data or not data.get('display_name'):
            return jsonify({'error': 'Missing display_name'}), 400
        
        new_display_name = data['display_name'].strip()
        
        if len(new_display_name) < 1 or len(new_display_name) > 50:
            return jsonify({'error': 'Display name must be between 1 and 50 characters'}), 400
        
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        user.display_name = new_display_name
        db.session.commit()
        
        return jsonify({
            'message': 'Display name updated successfully',
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/username', methods=['PUT'])
@jwt_required()
def update_username():
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data or not data.get('username'):
            return jsonify({'error': 'Missing username'}), 400
        
        new_username = data['username'].strip()
        
        if not is_valid_username(new_username):
            return jsonify({'error': 'Invalid username. Use 3-30 characters, letters, numbers and underscore only'}), 400
        
        existing_user = User.query.filter_by(username=new_username).first()
        if existing_user and existing_user.id != user_id:
            return jsonify({'error': 'Username already taken'}), 409
        
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        user.username = new_username
        db.session.commit()
        
        return jsonify({
            'message': 'Username updated successfully',
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/email', methods=['PUT'])
@jwt_required()
def update_email():
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data or not data.get('email'):
            return jsonify({'error': 'Missing email'}), 400
        
        new_email = data['email'].strip().lower()
        
        if not is_valid_email(new_email):
            return jsonify({'error': 'Invalid email format'}), 400
        
        existing_user = User.query.filter_by(email=new_email).first()
        if existing_user and existing_user.id != user_id:
            return jsonify({'error': 'Email already taken'}), 409
        
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        user.email = new_email
        db.session.commit()
        
        return jsonify({
            'message': 'Email updated successfully',
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/emoji', methods=['PUT'])
@jwt_required()
def set_profile_emoji():
    """Установка эмодзи рядом с именем в профиле"""
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        emoji_id = data.get('emoji_id')
        
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        if emoji_id:
            emoji = Emoji.query.get(emoji_id)
            if not emoji:
                return jsonify({'error': 'Emoji not found'}), 404
        
        user.selected_emoji_id = emoji_id
        db.session.commit()
        
        return jsonify({
            'message': 'Profile emoji updated successfully',
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ==================== ЭНДПОИНТЫ ЧАТОВ ====================

@app.route('/api/chats', methods=['GET'])
@jwt_required()
def get_chats():
    try:
        user_id = int(get_jwt_identity())
        
        chats = Chat.query.filter(
            (Chat.user1_id == user_id) | (Chat.user2_id == user_id)
        ).order_by(Chat.updated_at.desc()).all()
        
        return jsonify({
            'chats': [chat.to_dict(user_id) for chat in chats]
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chats/create', methods=['POST'])
@jwt_required()
def create_chat():
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data or not data.get('user_id'):
            return jsonify({'error': 'Missing user_id'}), 400
        
        other_user_id = int(data['user_id'])
        
        other_user = User.query.get(other_user_id)
        if not other_user:
            return jsonify({'error': 'User not found'}), 404
        
        if user_id == other_user_id:
            return jsonify({'error': 'Cannot create chat with yourself'}), 400
        
        existing_chat = Chat.query.filter(
            ((Chat.user1_id == user_id) & (Chat.user2_id == other_user_id)) |
            ((Chat.user1_id == other_user_id) & (Chat.user2_id == user_id))
        ).first()
        
        if existing_chat:
            return jsonify({
                'message': 'Chat already exists',
                'chat': existing_chat.to_dict(user_id)
            }), 200
        
        new_chat = Chat(
            user1_id=min(user_id, other_user_id), 
            user2_id=max(user_id, other_user_id)
        )
        
        db.session.add(new_chat)
        db.session.commit()
        
        return jsonify({
            'message': 'Chat created successfully',
            'chat': new_chat.to_dict(user_id)
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/chats/<int:chat_id>/messages', methods=['GET'])
@jwt_required()
def get_messages(chat_id):
    try:
        user_id = int(get_jwt_identity())
        
        chat = Chat.query.get(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        
        if chat.user1_id != user_id and chat.user2_id != user_id:
            return jsonify({'error': 'Access denied'}), 403
        
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        messages = Message.query.filter_by(chat_id=chat_id).order_by(
            Message.created_at.desc()
        ).paginate(page=page, per_page=per_page, error_out=False)
        
        unread_messages = Message.query.filter_by(
            chat_id=chat_id, 
            is_read=False
        ).filter(Message.sender_id != user_id).all()
        
        for msg in unread_messages:
            msg.is_read = True
        
        db.session.commit()
        
        return jsonify({
            'messages': [msg.to_dict() for msg in messages.items],
            'total': messages.total,
            'page': messages.page,
            'pages': messages.pages,
            'per_page': messages.per_page
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chats/<int:chat_id>/messages', methods=['POST'])
@jwt_required()
def send_message(chat_id):
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        chat = Chat.query.get(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        
        if chat.user1_id != user_id and chat.user2_id != user_id:
            return jsonify({'error': 'Access denied'}), 403
        
        receiver_id = chat.user2_id if chat.user1_id == user_id else chat.user1_id
        
        message_type = data.get('message_type', 'text')
        content = data.get('content')
        sticker_id = data.get('sticker_id')
        emoji_id = data.get('emoji_id')
        
        if message_type == 'text' and not content:
            return jsonify({'error': 'Message content is required'}), 400
        
        if message_type == 'sticker' and not sticker_id:
            return jsonify({'error': 'Sticker ID is required'}), 400
        
        if message_type == 'emoji' and not emoji_id:
            return jsonify({'error': 'Emoji ID is required'}), 400
        
        new_message = Message(
            chat_id=chat_id,
            sender_id=user_id,
            receiver_id=receiver_id,
            content=content,
            sticker_id=sticker_id,
            emoji_id=emoji_id,
            message_type=message_type
        )
        
        chat.updated_at = datetime.utcnow()
        
        db.session.add(new_message)
        db.session.commit()
        
        return jsonify({
            'message': 'Message sent successfully',
            'data': new_message.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ==================== ЭНДПОИНТЫ ДЛЯ СТИКЕРОВ ====================

@app.route('/api/stickers/packs', methods=['GET'])
def get_sticker_packs():
    """Получение списка всех доступных стикер-паков"""
    try:
        packs = StickerPack.query.filter_by(is_active=True).all()
        
        return jsonify({
            'packs': [pack.to_dict() for pack in packs]
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stickers/packs/<int:pack_id>/stickers', methods=['GET'])
def get_pack_stickers(pack_id):
    """Получение стикеров конкретного пака"""
    try:
        stickers = Sticker.query.filter_by(pack_id=pack_id).order_by(Sticker.order_index).all()
        
        return jsonify({
            'stickers': [s.to_dict() for s in stickers]
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stickers/packs/<int:pack_id>/download', methods=['POST'])
@jwt_required()
def download_sticker_pack(pack_id):
    """Скачивание стикер-пака пользователем"""
    try:
        user_id = int(get_jwt_identity())
        
        pack = StickerPack.query.get(pack_id)
        if not pack:
            return jsonify({'error': 'Pack not found'}), 404
        
        existing = UserStickerPack.query.filter_by(
            user_id=user_id, pack_id=pack_id
        ).first()
        
        if not existing:
            user_pack = UserStickerPack(
                user_id=user_id,
                pack_id=pack_id
            )
            db.session.add(user_pack)
            db.session.commit()
        
        return jsonify({'message': 'Pack downloaded successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stickers/user/packs', methods=['GET'])
@jwt_required()
def get_user_sticker_packs():
    """Получение списка скачанных пользователем стикер-паков"""
    try:
        user_id = int(get_jwt_identity())
        
        user_packs = UserStickerPack.query.filter_by(user_id=user_id).all()
        packs = [up.pack.to_dict() for up in user_packs]
        
        return jsonify({
            'packs': packs
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== ЭНДПОИНТЫ ДЛЯ ЭМОДЗИ ====================

@app.route('/api/emojis/packs', methods=['GET'])
def get_emoji_packs():
    """Получение списка всех доступных эмодзи-паков"""
    try:
        packs = EmojiPack.query.all()
        
        return jsonify({
            'packs': [pack.to_dict() for pack in packs]
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/emojis/packs/<int:pack_id>/emojis', methods=['GET'])
def get_pack_emojis(pack_id):
    """Получение эмодзи конкретного пака"""
    try:
        emojis = Emoji.query.filter_by(pack_id=pack_id).order_by(Emoji.order_index).all()
        
        return jsonify({
            'emojis': [e.to_dict() for e in emojis]
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/emojis/packs/<int:pack_id>/download', methods=['POST'])
@jwt_required()
def download_emoji_pack(pack_id):
    """Скачивание эмодзи-пака пользователем"""
    try:
        user_id = int(get_jwt_identity())
        
        pack = EmojiPack.query.get(pack_id)
        if not pack:
            return jsonify({'error': 'Pack not found'}), 404
        
        existing = UserEmojiPack.query.filter_by(
            user_id=user_id, pack_id=pack_id
        ).first()
        
        if not existing:
            user_pack = UserEmojiPack(
                user_id=user_id,
                pack_id=pack_id
            )
            db.session.add(user_pack)
            db.session.commit()
        
        return jsonify({'message': 'Pack downloaded successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/emojis/user/packs', methods=['GET'])
@jwt_required()
def get_user_emoji_packs():
    """Получение списка скачанных пользователем эмодзи-паков"""
    try:
        user_id = int(get_jwt_identity())
        
        user_packs = UserEmojiPack.query.filter_by(user_id=user_id).all()
        packs = [up.pack.to_dict() for up in user_packs]
        
        return jsonify({
            'packs': packs
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Запускаем синхронизацию при старте
with app.app_context():
    try:
        sync_with_content_server()
    except:
        print("⚠️ Сервер контента недоступен, продолжаем работу с существующими данными")

# Запуск приложения
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5004)