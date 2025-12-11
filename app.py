import os
import csv
import io
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# --- CONFIGURAZIONE DELL'APPLICAZIONE ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'chiave_segreta_panificio_super_sicura_900'  # Chiave di sessione per Flask
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///panificio.db'       # DB SQLite locale
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False                   # Evito warning inutili di SQLAlchemy

db = SQLAlchemy(app)

# Configurazione di Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Se non sono loggato, mi manda qui

# --- MODELLI DATABASE ---

class User(UserMixin, db.Model):
    # Modello utenti con ruoli (admin/user)
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')  # Gestione permessi base

class Product(db.Model):
    # Modello dei prodotti
    id = db.Column(db.Integer, primary_key=True)
    barcode = db.Column(db.String(50), unique=True, nullable=False)    # Identificativo univoco
    name = db.Column(db.String(100), nullable=False)
    brand = db.Column(db.String(100))
    supplier = db.Column(db.String(100))
    unit_measure = db.Column(db.String(20))
    unit_price = db.Column(db.Float, default=0.0)

    # Ogni prodotto può avere più lotti collegati
    batches = db.relationship(
        'Batch',
        backref='product',
        lazy=True,
        cascade="all, delete-orphan"   # Se elimino prodotto → elimina anche lotti
    )

    @property
    def total_quantity(self):
        # Sommo tutte le quantità correnti dei lotti
        return sum(b.quantity_current for b in self.batches)

class Batch(db.Model):
    # Modello dei lotti
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_initial = db.Column(db.Float, nullable=False)  # Quantità iniziale
    quantity_current = db.Column(db.Float, nullable=False)  # Quantità attuale
    entry_date = db.Column(db.DateTime, default=datetime.utcnow)  # Quando ho aggiunto il lotto
    expiry_date = db.Column(db.Date, nullable=True)               # Scadenza (facoltativa)
    created_by = db.Column(db.String(100))                        # Utente che ha caricato il lotto

class Log(db.Model):
    # Log delle operazioni (carico/scarico/creazione)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100))
    product_name = db.Column(db.String(100))
    action = db.Column(db.String(10))          # CREATE, IN, OUT
    quantity_change = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# --- FUNZIONI UTILI ---

@login_manager.user_loader
def load_user(user_id):
    # Funzione richiesta da Flask-Login per recuperare l’utente loggato
    return User.query.get(int(user_id))

def create_admin():
    # Mi assicuro che l'admin esista alla prima esecuzione
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', password_hash=generate_password_hash('admin123'), role='admin')
            db.session.add(admin)
            db.session.commit()
            print("Utente Admin creato.")

# --- ROTTE PRINCIPALI ---

@app.route('/')
@login_required
def index():
    # Reindirizzo subito alla dashboard
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Login degli utenti
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        # Controllo credenziali
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))

        flash('Credenziali non valide', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    # Logout e distruzione sessione
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Vista principale con le azioni rapide
    return render_template('dashboard.html')

@app.route('/inventory')
@login_required
def inventory():
    # Visualizzo tutti i prodotti
    products = Product.query.all()
    return render_template('inventory.html', products=products)

@app.route('/scan/<mode>')
@login_required
def scan_action(mode):
    # Pagina che prepara lo scanner in modalità "in" o "out"
    return render_template('scan_action.html', mode=mode)

@app.route('/handle_scan', methods=['GET'])
@login_required
def handle_scan():
    # Gestisco il codice letto dallo scanner
    code = request.args.get('code')
    mode = request.args.get('mode')

    product = Product.query.filter_by(barcode=code).first()

    # Modalità carico
    if mode == 'in':
        if product:
            # Se esiste, apro la pagina del prodotto
            return redirect(url_for('product_detail', id=product.id))
        else:
            # Altrimenti lo creo
            return redirect(url_for('create_product', code=code))

    # Modalità scarico
    elif mode == 'out':
        if product:
            return redirect(url_for('product_detail', id=product.id))
        else:
            flash('Prodotto non trovato in magazzino!', 'warning')
            return redirect(url_for('dashboard'))

    return redirect(url_for('dashboard'))

@app.route('/product/new', methods=['GET', 'POST'])
@login_required
def create_product():
    # Creazione nuovo prodotto
    code = request.args.get('code', '')

    if request.method == 'POST':
        barcode = request.form.get('barcode')
        name = request.form.get('name')

        # Evito codici duplicati
        existing = Product.query.filter_by(barcode=barcode).first()
        if existing:
            flash('Codice a barre già esistente!', 'danger')
            return redirect(url_for('inventory'))

        # Creo il prodotto
        new_prod = Product(
            barcode=barcode,
            name=name,
            brand=request.form.get('brand'),
            supplier=request.form.get('supplier'),
            unit_measure=request.form.get('unit_measure'),
            unit_price=float(request.form.get('unit_price', 0))
        )

        db.session.add(new_prod)

        # Log dell’operazione
        log = Log(user_id=current_user.username, product_name=name, action="CREATE", quantity_change=0)
        db.session.add(log)

        db.session.commit()

        flash('Prodotto creato!', 'success')
        return redirect(url_for('product_detail', id=new_prod.id))

    return render_template('create_product.html', code=code)

# --- MODIFICA E CANCELLAZIONE PRODOTTI ---

@app.route('/product/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(id):
    # Solo admin possono modificare
    if current_user.role != 'admin':
        flash('Accesso negato: Solo gli amministratori possono modificare i prodotti.', 'danger')
        return redirect(url_for('product_detail', id=id))

    product = Product.query.get_or_404(id)

    if request.method == 'POST':
        new_barcode = request.form.get('barcode')

        # Controllo codice duplicato
        if new_barcode != product.barcode:
            existing = Product.query.filter_by(barcode=new_barcode).first()
            if existing:
                flash('Errore: Questo codice a barre è già usato da un altro prodotto!', 'danger')
                return render_template('edit_product.html', product=product)

        # Aggiorno i campi base
        product.barcode = new_barcode
        product.name = request.form.get('name')
        product.brand = request.form.get('brand')
        product.supplier = request.form.get('supplier')
        product.unit_measure = request.form.get('unit_measure')

        # Controllo sicurezza sul prezzo
        try:
            product.unit_price = float(request.form.get('unit_price', 0))
        except ValueError:
            product.unit_price = 0.0

        db.session.commit()

        flash('Prodotto aggiornato con successo.', 'success')
        return redirect(url_for('product_detail', id=product.id))

    return render_template('edit_product.html', product=product)

@app.route('/product/<int:id>/delete', methods=['POST'])
@login_required
def delete_product(id):
    # Cancella prodotto + tutti i suoi lotti (grazie a cascade)
    if current_user.role != 'admin':
        flash('Accesso negato.', 'danger')
        return redirect(url_for('inventory'))

    product = Product.query.get_or_404(id)
    nome_prodotto = product.name

    db.session.delete(product)
    db.session.commit()

    flash(f'Prodotto "{nome_prodotto}" e tutti i suoi lotti sono stati eliminati.', 'success')
    return redirect(url_for('inventory'))

# --- DETTAGLIO PRODOTTO ---

@app.route('/product/<int:id>', methods=['GET', 'POST'])
@login_required
def product_detail(id):
    # Pagina per visualizzare un prodotto e i suoi lotti
    product = Product.query.get_or_404(id)

    # Mostro solo lotti con quantità > 0 e li ordino per scadenza
    batches = Batch.query.filter_by(product_id=id)\
        .filter(Batch.quantity_current > 0)\
        .order_by(Batch.expiry_date.asc())\
        .all()

    return render_template('product_detail.html', product=product, batches=batches)

@app.route('/add_batch/<int:product_id>', methods=['POST'])
@login_required
def add_batch(product_id):
    # Aggiungo un lotto a un prodotto
    product = Product.query.get_or_404(product_id)
    qty = float(request.form.get('quantity'))
    expiry = request.form.get('expiry_date')

    # Valido la data di scadenza
    if expiry:
        expiry_date = datetime.strptime(expiry, '%Y-%m-%d').date()
    else:
        expiry_date = None

    # Creo il lotto
    new_batch = Batch(
        product_id=product.id,
        quantity_initial=qty,
        quantity_current=qty,
        expiry_date=expiry_date,
        created_by=current_user.username
    )

    db.session.add(new_batch)

    # Log caricamento
    log = Log(user_id=current_user.username, product_name=product.name, action="IN", quantity_change=qty)
    db.session.add(log)

    db.session.commit()

    flash(f'Caricati {qty} {product.unit_measure}', 'success')
    return redirect(url_for('product_detail', id=product.id))

@app.route('/use_batch/<int:batch_id>', methods=['POST'])
@login_required
def use_batch(batch_id):
    # Scarico quantità da un lotto
    batch = Batch.query.get_or_404(batch_id)

    qty_to_use = float(request.form.get('quantity_use'))

    if qty_to_use > batch.quantity_current:
        # Evito quantità negative
        flash('Quantità insufficiente!', 'danger')
        return redirect(url_for('product_detail', id=batch.product_id))

    batch.quantity_current -= qty_to_use

    # Log scarico
    log = Log(user_id=current_user.username, product_name=batch.product.name, action="OUT", quantity_change=qty_to_use)
    db.session.add(log)

    db.session.commit()

    flash('Scarico effettuato.', 'success')
    return redirect(url_for('product_detail', id=batch.product_id))

# --- GESTIONE UTENTI (SOLO ADMIN) ---

@app.route('/admin/users', methods=['GET', 'POST'])
@login_required
def admin_users():
    # Solo gli admin possono gestire utenti
    if current_user.role != 'admin':
        flash('Accesso negato', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')

        # Creazione nuovo utente
        if action == 'create':
            username = request.form.get('username')
            password = request.form.get('password')
            role = request.form.get('role')

            # Controllo duplicati
            if User.query.filter_by(username=username).first():
                flash('Username già in uso', 'danger')
            else:
                new_user = User(username=username, password_hash=generate_password_hash(password), role=role)
                db.session.add(new_user)
                db.session.commit()
                flash('Utente creato', 'success')

        # Eliminazione utente
        elif action == 'delete':
            user_id = request.form.get('user_id')
            User.query.filter_by(id=user_id).delete()
            db.session.commit()
            flash('Utente eliminato', 'warning')

    users = User.query.all()
    return render_template('admin_users.html', users=users)

# --- ESPORTAZIONE CSV ---

@app.route('/export_csv')
@login_required
def export_csv():
    # Esporto inventario in CSV
    products = Product.query.all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Intestazione
    writer.writerow(['Codice', 'Prodotto', 'Marca', 'Fornitore', 'Giacenza Totale', 'Unita', 'Prezzo Unitario', 'Valore Totale'])

    # Righe dei prodotti
    for p in products:
        valore = p.total_quantity * p.unit_price
        writer.writerow([p.barcode, p.name, p.brand, p.supplier, p.total_quantity, p.unit_measure, p.unit_price, valore])

    output.seek(0)

    # Restituisco file CSV come download
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'inventario_{datetime.now().strftime("%Y%m%d")}.csv'
    )

# --- AVVIO DELL'APPLICAZIONE ---
if __name__ == '__main__':
    create_admin()  # Mi assicuro che l'admin esista
    app.run(host='0.0.0.0', port=8000, debug=True, ssl_context='adhoc')  # HTTPS auto-generato
