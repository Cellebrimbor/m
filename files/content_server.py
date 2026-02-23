import os
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid
import zipfile
import shutil
from werkzeug.utils import secure_filename
from pathlib import Path

# Создаем приложение
app = Flask(__name__)

# Конфигурация
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///content.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'content'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max

# Инициализация
db = SQLAlchemy(app)

# Создаем папки для контента
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'stickers'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'emojis'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'packs'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'temp'), exist_ok=True)

# ==================== МОДЕЛИ ДАННЫХ ====================

class StickerPack(db.Model):
    __tablename__ = 'sticker_packs'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    icon_path = db.Column(db.String(500))
    preview_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_default = db.Column(db.Boolean, default=False)
    stickers_count = db.Column(db.Integer, default=0)
    
    stickers = db.relationship('Sticker', backref='pack', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'external_id': self.external_id,
            'name': self.name,
            'description': self.description,
            'icon_path': self.icon_path,
            'preview_path': self.preview_path,
            'stickers_count': self.stickers_count,
            'is_default': self.is_default,
            'stickers': [s.to_dict() for s in self.stickers]
        }

class Sticker(db.Model):
    __tablename__ = 'stickers'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True, default=lambda: str(uuid.uuid4()))
    pack_id = db.Column(db.Integer, db.ForeignKey('sticker_packs.id'), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    image_path = db.Column(db.String(500), nullable=False)
    emoji = db.Column(db.String(10))
    order_index = db.Column(db.Integer, default=0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'external_id': self.external_id,
            'pack_id': self.pack_id,
            'file_name': self.file_name,
            'image_path': self.image_path,
            'emoji': self.emoji,
            'order_index': self.order_index
        }

class EmojiPack(db.Model):
    __tablename__ = 'emoji_packs'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True, default=lambda: str(uuid.uuid4()))
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
            'icon_path': self.icon_path,
            'emojis_count': self.emojis_count,
            'is_default': self.is_default,
            'emojis': [e.to_dict() for e in self.emojis]
        }

class Emoji(db.Model):
    __tablename__ = 'emojis'
    
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True, default=lambda: str(uuid.uuid4()))
    pack_id = db.Column(db.Integer, db.ForeignKey('emoji_packs.id'), nullable=False)
    character = db.Column(db.String(10))
    image_path = db.Column(db.String(500))
    name = db.Column(db.String(100))
    order_index = db.Column(db.Integer, default=0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'external_id': self.external_id,
            'pack_id': self.pack_id,
            'character': self.character,
            'image_path': self.image_path,
            'name': self.name,
            'order_index': self.order_index
        }

# Создание таблиц
with app.app_context():
    db.create_all()
    print("✅ База данных контента инициализирована!")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def extract_zip(zip_path, extract_to):
    """Распаковка ZIP архива"""
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

@app.route('/api/admin/emojis/upload', methods=['POST'])
def upload_emoji_pack():
    """Загрузка нового эмодзи-пака (ZIP архив)"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        name = request.form.get('name', '')
        description = request.form.get('description', '')
        
        if file.filename == '':
            return jsonify({'error': 'Empty filename'}), 400
        
        if not file.filename.endswith('.zip'):
            return jsonify({'error': 'File must be ZIP archive'}), 400
        
        # Сохраняем ZIP
        filename = secure_filename(file.filename)
        zip_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp', f"{uuid.uuid4()}.zip")
        file.save(zip_path)
        
        # Создаем папку для пака
        pack_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'emojis', str(uuid.uuid4()))
        os.makedirs(pack_dir, exist_ok=True)
        
        # Распаковываем
        extract_zip(zip_path, pack_dir)
        
        # Ищем иконку
        icon_path = None
        for f in os.listdir(pack_dir):
            if f.startswith('icon') and f.endswith(('.png', '.jpg', '.webp')):
                icon_path = f"/content/emojis/{os.path.basename(pack_dir)}/{f}"
                break
        
        # Считаем количество изображений (исключая иконку)
        image_files = [f for f in os.listdir(pack_dir) 
                      if f.endswith(('.png', '.jpg', '.webp')) and not f.startswith('icon')]
        
        # Создаем записи в БД
        pack = EmojiPack(
            name=name,
            description=description,
            icon_path=icon_path,
            emojis_count=len(image_files)
        )
        db.session.add(pack)
        db.session.flush()
        
        # Добавляем эмодзи
        for i, file_name in enumerate(image_files):
            name_without_ext = os.path.splitext(file_name)[0]
            
            emoji = Emoji(
                pack_id=pack.id,
                character=None,  # null для кастомных эмодзи
                image_path=f"/content/emojis/{os.path.basename(pack_dir)}/{file_name}",
                name=name_without_ext,
                order_index=i
            )
            db.session.add(emoji)
        
        db.session.commit()
        
        # Удаляем ZIP
        os.remove(zip_path)
        
        return jsonify({
            'message': 'Emoji pack uploaded successfully',
            'pack': pack.to_dict()
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def create_sample_packs():
    """Создание тестовых паков для примера"""
    
    # Проверяем, есть ли уже паки
    if StickerPack.query.count() > 0 or EmojiPack.query.count() > 0:
        return
    
    print("📦 Создание тестовых паков...")
    
    # Создаем папки для тестовых файлов
    stickers_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'stickers', 'sample')
    emojis_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'emojis', 'sample')
    os.makedirs(stickers_dir, exist_ok=True)
    os.makedirs(emojis_dir, exist_ok=True)
    
    # Создаем тестовые стикер-паки
    sticker_packs = [
        {
            'name': 'Котики',
            'description': 'Милые котики для любого чата',
            'is_default': True,
            'stickers': [
                {'file_name': 'cat1.png', 'emoji': '🐱'},
                {'file_name': 'cat2.png', 'emoji': '😺'},
                {'file_name': 'cat3.png', 'emoji': '😸'},
            ]
        },
        {
            'name': 'Мемы',
            'description': 'Популярные мемы',
            'is_default': True,
            'stickers': [
                {'file_name': 'meme1.png', 'emoji': '😂'},
                {'file_name': 'meme2.png', 'emoji': '😅'},
            ]
        }
    ]
    
    for pack_data in sticker_packs:
        pack = StickerPack(
            name=pack_data['name'],
            description=pack_data['description'],
            icon_path=f"/content/stickers/sample/{pack_data['name']}/icon.png",
            preview_path=f"/content/stickers/sample/{pack_data['name']}/preview.png",
            is_default=pack_data['is_default'],
            stickers_count=len(pack_data['stickers'])
        )
        db.session.add(pack)
        db.session.flush()
        
        pack_dir = os.path.join(stickers_dir, pack_data['name'])
        os.makedirs(pack_dir, exist_ok=True)
        
        for i, sticker_data in enumerate(pack_data['stickers']):
            sticker_path = f"/content/stickers/sample/{pack_data['name']}/{sticker_data['file_name']}"
            sticker = Sticker(
                pack_id=pack.id,
                file_name=sticker_data['file_name'],
                image_path=sticker_path,
                emoji=sticker_data['emoji'],
                order_index=i
            )
            db.session.add(sticker)
    
    # Создаем тестовые эмодзи-паки
    emoji_packs = [
        {
            'name': 'Классические',
            'description': 'Стандартные эмодзи',
            'is_default': True,
            'emojis': [
                {'character': '😊', 'name': 'smile'},
                {'character': '❤️', 'name': 'heart'},
                {'character': '😂', 'name': 'laugh'},
                {'character': '👍', 'name': 'thumbs_up'},
                {'character': '🎉', 'name': 'party'},
            ]
        },
        {
            'name': 'Животные',
            'description': 'Эмодзи с животными',
            'is_default': True,
            'emojis': [
                {'character': '🐱', 'name': 'cat'},
                {'character': '🐶', 'name': 'dog'},
                {'character': '🐼', 'name': 'panda'},
                {'character': '🦊', 'name': 'fox'},
            ]
        }
    ]
    
    for pack_data in emoji_packs:
        pack = EmojiPack(
            name=pack_data['name'],
            description=pack_data['description'],
            icon_path=f"/content/emojis/sample/{pack_data['name']}/icon.png",
            is_default=pack_data['is_default'],
            emojis_count=len(pack_data['emojis'])
        )
        db.session.add(pack)
        db.session.flush()
        
        for i, emoji_data in enumerate(pack_data['emojis']):
            emoji = Emoji(
                pack_id=pack.id,
                character=emoji_data['character'],
                name=emoji_data['name'],
                order_index=i
            )
            db.session.add(emoji)
    
    db.session.commit()
    print("✅ Тестовые паки созданы")

# ==================== ЭНДПОИНТЫ ====================

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'message': 'Content server is running'}), 200

@app.route('/api/content/packs', methods=['GET'])
def get_all_packs():
    """Получение всех паков (для синхронизации с основным сервером)"""
    try:
        sticker_packs = StickerPack.query.all()
        emoji_packs = EmojiPack.query.all()
        
        return jsonify({
            'sticker_packs': [pack.to_dict() for pack in sticker_packs],
            'emoji_packs': [pack.to_dict() for pack in emoji_packs]
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stickers/packs', methods=['GET'])
def get_sticker_packs():
    """Получение всех стикер-паков"""
    try:
        packs = StickerPack.query.all()
        return jsonify({
            'packs': [pack.to_dict() for pack in packs]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stickers/packs/<int:pack_id>/stickers', methods=['GET'])
def get_pack_stickers(pack_id):
    """Получение стикеров пака"""
    try:
        stickers = Sticker.query.filter_by(pack_id=pack_id).order_by(Sticker.order_index).all()
        return jsonify({
            'stickers': [s.to_dict() for s in stickers]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/emojis/packs', methods=['GET'])
def get_emoji_packs():
    """Получение всех эмодзи-паков"""
    try:
        packs = EmojiPack.query.all()
        return jsonify({
            'packs': [pack.to_dict() for pack in packs]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/emojis/packs/<int:pack_id>/emojis', methods=['GET'])
def get_pack_emojis(pack_id):
    """Получение эмодзи пака"""
    try:
        emojis = Emoji.query.filter_by(pack_id=pack_id).order_by(Emoji.order_index).all()
        return jsonify({
            'emojis': [e.to_dict() for e in emojis]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Эндпоинты для получения файлов
@app.route('/content/stickers/<path:filename>')
def get_sticker_file(filename):
    """Получение файла стикера"""
    return send_from_directory(
        os.path.join(app.config['UPLOAD_FOLDER'], 'stickers'),
        filename
    )

@app.route('/content/emojis/<path:filename>')
def get_emoji_file(filename):
    """Получение файла эмодзи"""
    return send_from_directory(
        os.path.join(app.config['UPLOAD_FOLDER'], 'emojis'),
        filename
    )

@app.route('/content/packs/<path:filename>')
def get_pack_file(filename):
    """Получение файла пака (иконки, превью)"""
    return send_from_directory(
        os.path.join(app.config['UPLOAD_FOLDER'], 'packs'),
        filename
    )

# Эндпоинты для загрузки новых паков (для администрирования)
@app.route('/api/admin/stickers/upload', methods=['POST'])
def upload_sticker_pack():
    """Загрузка нового стикер-пака (ZIP архив)"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        name = request.form.get('name', '')
        description = request.form.get('description', '')
        
        if file.filename == '':
            return jsonify({'error': 'Empty filename'}), 400
        
        if not file.filename.endswith('.zip'):
            return jsonify({'error': 'File must be ZIP archive'}), 400
        
        # Сохраняем ZIP
        filename = secure_filename(file.filename)
        zip_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp', f"{uuid.uuid4()}.zip")
        file.save(zip_path)
        
        # Создаем папку для пака
        pack_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'stickers', str(uuid.uuid4()))
        os.makedirs(pack_dir, exist_ok=True)
        
        # Распаковываем
        extract_zip(zip_path, pack_dir)
        
        # Создаем записи в БД
        pack = StickerPack(
            name=name,
            description=description,
            icon_path=f"/content/stickers/{os.path.basename(pack_dir)}/icon.png",
            stickers_count=len([f for f in os.listdir(pack_dir) if f.endswith(('.png', '.jpg', '.webp'))])
        )
        db.session.add(pack)
        db.session.flush()
        
        # Добавляем стикеры
        for i, file_name in enumerate(os.listdir(pack_dir)):
            if file_name.endswith(('.png', '.jpg', '.webp')):
                sticker = Sticker(
                    pack_id=pack.id,
                    file_name=file_name,
                    image_path=f"/content/stickers/{os.path.basename(pack_dir)}/{file_name}",
                    order_index=i
                )
                db.session.add(sticker)
        
        db.session.commit()
        
        # Удаляем ZIP
        os.remove(zip_path)
        
        return jsonify({
            'message': 'Pack uploaded successfully',
            'pack': pack.to_dict()
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Создаем тестовые данные при первом запуске
with app.app_context():
    create_sample_packs()

# Запуск приложения
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5005)