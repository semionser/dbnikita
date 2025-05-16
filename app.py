import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from io import BytesIO
from threading import Thread
import pandas as pd
import telebot
from dotenv import load_dotenv
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone

# === Загрузка переменных окружения ===
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID"))

# === Проверка токена ===
if not TOKEN or not GROUP_ID:
    raise ValueError("TELEGRAM_BOT_TOKEN и TELEGRAM_GROUP_ID должны быть заданы в .env файле!")

# === Инициализация Flask ===
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///databasenikita.db'  # Используем SQLite
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = '/var/www/db1/static/uploads'  # Полный путь для загрузки
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Замените на свой секретный ключ


db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# === Telegram Bot ===
bot = telebot.TeleBot(TOKEN)

# === Модели ===

#== бд входа ===
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

#== бд продуктов ===
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    image = db.Column(db.String(100), nullable=True)
    category = db.Column(db.String(100), nullable=False, default='товар')

#== бд тасков ===
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=False)
    deadline = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

#== авторизация
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

#== добавление пароля в бд ==
def create_admin_user():
    if not User.query.filter_by(username='adm').first():
        hashed_password = generate_password_hash('1234')
        db.session.add(User(username='adm', password=hashed_password))
        db.session.commit()


# === Маршруты ===

#== индекс===
@app.route("/", methods=["GET"])
def index():
    search_query = request.args.get('search', '').strip().lower()
    all_products = Product.query.all()

    if search_query:
        products = [product for product in all_products if search_query in product.name.lower()]
    else:
        products = all_products

    return render_template("index.html", products=products)


#== обновление продуктов ===
@app.route('/update/<int:product_id>', methods=['POST'])
@login_required
def update_quantity(product_id):
    product = Product.query.get_or_404(product_id)
    new_quantity = request.form.get('quantity')
    if new_quantity.isdigit():
        product.quantity = int(new_quantity)
        db.session.commit()
    return redirect(url_for('index'))


#== удаление продуктов ===
@app.route('/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for('index'))


#== добавление продуктов ===
@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        name = request.form['name']
        quantity = int(request.form['quantity'])
        category = request.form['category']
        image_file = request.files['image']
        filename = None
        if image_file:
            filename = secure_filename(image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        new_product = Product(name=name, quantity=quantity, category=category, image=filename)
        db.session.add(new_product)
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('add_product.html')


#== авторизация===
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('index'))
        error = 'Неверный логин или пароль'
    return render_template('login.html', error=error)



#== выход в авторизацию ===
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


#==  Таски ===
NAMES = ['Вася', 'Сема', 'Рома']

@app.route('/tasks', methods=['GET', 'POST'])
@login_required
def tasks():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        deadline_str = request.form.get('deadline')

        if not (name and description and deadline_str):
            flash('Все поля обязательны')
            return redirect(url_for('tasks'))

        try:
            deadline = datetime.strptime(deadline_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Некорректный формат даты. Используйте ГГГГ-ММ-ДД.')
            return redirect(url_for('tasks'))

        new_task = Task(name=name, description=description, deadline=deadline)
        db.session.add(new_task)
        db.session.commit()
        flash('Задача добавлена')
        return redirect(url_for('tasks'))

    tasks_list = Task.query.order_by(Task.deadline).all()
    today = date.today()

    now = datetime.now()
    next_send_time = datetime.combine(now.date(), datetime.strptime("10:00", "%H:%M").time())
    if now > next_send_time:
        next_send_time += timedelta(days=1)
    hours_until_send = (next_send_time - now).seconds // 3600

    return render_template('tasks.html', tasks=tasks_list, today=today, names=NAMES, hours_until_send=hours_until_send)


@app.route('/tasks/delete/<int:task_id>', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    flash('Задача удалена')
    return redirect(url_for('tasks'))


@app.route('/notify_tasks', methods=['POST'])
@login_required
def notify_tasks():
    send_task_info()
    flash('Задачи отправлены в Telegram-группу!')
    return redirect(url_for('tasks'))


def send_task_info():
    with app.app_context():
        tasks = Task.query.order_by(Task.deadline).all()
        today = date.today()
        if not tasks:
            bot.send_message(chat_id=GROUP_ID, text="Список задач пуст.")
            return

        msg = "📋 *Список задач:*\n\n"
        for task in tasks:
            days_left = (task.deadline - today).days
            if days_left < 0:
                status = "⛔️ *ПРОСРОЧЕНО*"
            elif days_left == 0:
                status = "⚠️ *Сегодня*"
            elif days_left == 1:
                status = f"❗️ *{days_left} день остался*"
            else:
                status = f"⏳ {days_left} дн."

            msg += f"*{task.name}* — {task.description}\nДо {task.deadline.strftime('%Y-%m-%d')} ({status})\n\n"

        bot.send_message(chat_id=GROUP_ID, text=msg, parse_mode='Markdown')


# === Планировщик отправки в 11:00 ===
scheduler = BackgroundScheduler(timezone=timezone('Europe/Moscow'))

@scheduler.scheduled_job('cron', hour=10, minute=0)
def scheduled_task_sender():
    with app.app_context():
        send_task_info()

#== аналитика ===
@app.route('/analytics')
@login_required
def analytics():
    products = Product.query.all()
    total_goods_quantity = sum(p.quantity for p in products if p.category == 'товар')
    added_goods_count = sum(1 for p in products if p.category == 'товар')
    added_stationery_count = sum(1 for p in products if p.category == 'канцелярия')
    return render_template('analytics.html',
                           total_goods_quantity=total_goods_quantity,
                           added_goods_count=added_goods_count,
                           added_stationery_count=added_stationery_count)


#== экслеь кнопка ===
@app.route('/download_excel')
@login_required
def download_excel():
    products = Product.query.all()
    rows = []
    goods = [p for p in products if p.category == 'товар']
    stationery = [p for p in products if p.category == 'канцелярия']
    if goods:
        rows += [['Товары'], ['Название', 'Количество']]
        rows += [[p.name, p.quantity] for p in goods]
    if stationery:
        rows += [[], ['Канцелярия'], ['Название', 'Количество']]
        rows += [[p.name, p.quantity] for p in stationery]
    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, header=False, sheet_name='Склад')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="products.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


#== телеграм кнопка ===
@app.route('/notify_stock', methods=['POST'])
@login_required
def notify_stock():
    send_stock_info()
    flash('Оповещение отправлено в Telegram-группу!')
    return redirect(url_for('analytics'))


# === Отправка остатков телеграм  ===
def send_stock_info():
    with app.app_context():
        products = Product.query.all()
        goods = [p for p in products if p.category == 'товар']
        stationery = [p for p in products if p.category == 'канцелярия']
        msg = "Остатки на складе:\n\n"
        if goods:
            msg += "Товары:\n" + "\n".join(f"- {p.name}: {p.quantity}" for p in goods)
        if stationery:
            msg += "\n\nКанцелярия:\n" + "\n".join(f"- {p.name}: {p.quantity}" for p in stationery)
        bot.send_message(chat_id=GROUP_ID, text=msg)

# === Обработка команды /остатки в Telegram ===
@bot.message_handler(commands=['остатки'])
def handle_stock_command(message):
    if message.chat.id == GROUP_ID:
        send_stock_info()


# === Запуск бота и сервера ===
def run_bot():
    bot.polling(none_stop=True)

scheduler.start()
if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
        create_admin_user()
    Thread(target=run_bot).start()
    app.run(host='0.0.0.0', port=5000)