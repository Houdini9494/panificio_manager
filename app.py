import os
import csv
import io
from datetime import datetime
# Importiamo Flask e le sue estensioni necessarie
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# --- CONFIGURAZIONE DELL'APPLICAZIONE ---
# Inizializzazione l'app Flask
app = Flask(__name__)

# Chiave segreta per proteggere le sessioni e i cookie (fondamentale per la sicurezza)
app.config['SECRET_KEY'] = 'chiave_segreta_panificio_super_sicura_900'

# Percorso del database SQLite (verrà creato nella cartella 'instance')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///panificio.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inizializzazione database e gestore dei login
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
# Se un utente non loggato prova ad accedere, lo rimandiamo alla pagina 'login'
login_manager.login_view = 'login'

# --- MODELLI DEL DATABASE (LE TABELLE) ---

# Tabella UTENTI: gestisce chi può accedere all'app
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False) # Username univoco
    password_hash = db.Column(db.String(200), nullable=False)         # Password criptata (non salviamo mai in chiaro)
    role = db.Column(db.String(20), default='user')                   # Ruolo: 'admin' può fare tutto, 'user' solo caricare/scaricare

# Tabella PRODOTTI: l'anagrafica generale (es. Farina, Lievito)
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    barcode = db.Column(db.String(50), unique=True, nullable=False)   # Codice a barre univoco
    name = db.Column(db.String(100), nullable=False)                  # Nome prodotto
    brand = db.Column(db.String(100))                                 # Marca
    supplier = db.Column(db.String(100))                              # Fornitore
    unit_measure = db.Column(db.String(20))                           # Unità (Kg, L, Pz)
    unit_price = db.Column(db.Float, default=0.0)                     # Prezzo unitario
    # Relazione: un prodotto può avere molti lotti (Batch)
    batches = db.relationship('Batch', backref='product', lazy=True, cascade="all, delete-orphan")

    # Proprietà calcolata: somma automatica di tutte le quantità dei lotti attivi
    @property
    def total_quantity(self):
        return sum(b.quantity_current for b in self.batches)

# Tabella LOTTI (BATCH): gestisce gli arrivi di merce con date diverse
class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False) # Collegamento al prodotto
    quantity_initial = db.Column(db.Float, nullable=False)            # Quanto ne è arrivato all'inizio
    quantity_current = db.Column(db.Float, nullable=False)            # Quanto ne rimane ora (cala quando scarichiamo)
    entry_date = db.Column(db.DateTime, default=datetime.utcnow)      # Data di arrivo (automatica)
    expiry_date = db.Column(db.Date, nullable=True)                   # Data di scadenza
    created_by = db.Column(db.String(100))                            # Chi ha caricato questo lotto

# Tabella LOG: storico di tutte le operazioni (chi ha fatto cosa)
class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100))    # Nome utente
    product_name = db.Column(db.String(100))
    action = db.Column(db.String(10))      # Tipo azione: IN (Carico), OUT (Scarico), CREATE
    quantity_change = db.Column(db.Float)  # Quantità movimentata
    timestamp = db.Column(db.DateTime, default=datetime.utcnow) # Quando è successo

# --- GESTIONE UTENTI E AVVIO ---

# Funzione che serve a Flask-Login per recuperare l'utente corrente
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Funzione eseguita all'avvio: crea il database e l'utente Admin se non esiste
def create_admin():
    with app.app_context():
        db.create_all() # Crea le tabelle se non esistono
        if not User.query.filter_by(username='admin').first():
            # Creazione utente amministratore di default
            admin = User(username='admin', password_hash=generate_password_hash('admin123'), role='admin')
            db.session.add(admin)
            db.session.commit()
            print("Utente Admin creato: user=admin, pass=admin123")

# --- ROTTE (LE PAGINE DEL SITO) ---

# Rotta principale: reindirizza subito alla dashboard
@app.route('/')
@login_required
def index():
    return redirect(url_for('dashboard'))

# Pagina di Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        # Verifica se utente esiste e password coincide
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Credenziali non valide', 'danger')
    return render_template('login.html')

# Logout: disconnette l'utente
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Dashboard: il menu principale con i pulsantoni
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

# Inventario: lista di tutti i prodotti
@app.route('/inventory')
@login_required
def inventory():
    products = Product.query.all()
    return render_template('inventory.html', products=products)

# Pagina di Scansione: gestisce sia Carico ('in') che Scarico ('out')
@app.route('/scan/<mode>')
@login_required
def scan_action(mode):
    return render_template('scan_action.html', mode=mode)

# Gestore Scansione: riceve il codice letto e decide dove mandare l'utente
@app.route('/handle_scan', methods=['GET'])
@login_required
def handle_scan():
    code = request.args.get('code') # Codice letto
    mode = request.args.get('mode') # Modalità (in/out)

    product = Product.query.filter_by(barcode=code).first()

    if mode == 'in':
        # SE STIAMO CARICANDO:
        if product:
            # Il prodotto esiste già -> andiamo ad aggiungere un lotto
            return redirect(url_for('product_detail', id=product.id))
        else:
            # Il prodotto è nuovo -> andiamo a crearlo
            return redirect(url_for('create_product', code=code))

    elif mode == 'out':
        # SE STIAMO SCARICANDO:
        if product:
            return redirect(url_for('product_detail', id=product.id))
        else:
            flash('Prodotto non trovato in magazzino!', 'warning')
            return redirect(url_for('dashboard'))

    return redirect(url_for('dashboard'))

# Creazione Nuovo Prodotto (Anagrafica)
@app.route('/product/new', methods=['GET', 'POST'])
@login_required
def create_product():
    code = request.args.get('code', '')
    if request.method == 'POST':
        # Salvataggio dati del form
        barcode = request.form.get('barcode')
        name = request.form.get('name')

        # Controllo duplicati
        existing = Product.query.filter_by(barcode=barcode).first()
        if existing:
            flash('Codice a barre già esistente!', 'danger')
            return redirect(url_for('inventory'))

        # Creazione oggetto Prodotto
        new_prod = Product(
            barcode=barcode,
            name=name,
            brand=request.form.get('brand'),
            supplier=request.form.get('supplier'),
            unit_measure=request.form.get('unit_measure'),
            unit_price=float(request.form.get('unit_price', 0))
        )
        db.session.add(new_prod)

        # Registrazione Log
        log = Log(user_id=current_user.username, product_name=name, action="CREATE", quantity_change=0)
        db.session.add(log)

        db.session.commit()
        flash('Prodotto creato! Ora aggiungi il primo lotto.', 'success')
        return redirect(url_for('product_detail', id=new_prod.id))

    return render_template('create_product.html', code=code)

# Pagina Dettaglio Prodotto (dove si vedono i Lotti)
@app.route('/product/<int:id>', methods=['GET', 'POST'])
@login_required
def product_detail(id):
    product = Product.query.get_or_404(id)
    
    # Recupera i lotti attivi (quantità > 0), ordinati per scadenza (FIFO)
    batches = Batch.query.filter_by(product_id=id).filter(Batch.quantity_current > 0).order_by(Batch.expiry_date.asc()).all()
    
    return render_template('product_detail.html', product=product, batches=batches)

# Aggiunta di un Lotto (Carico Merce)
@app.route('/add_batch/<int:product_id>', methods=['POST'])
@login_required
def add_batch(product_id):
    product = Product.query.get_or_404(product_id)
    qty = float(request.form.get('quantity'))
    expiry = request.form.get('expiry_date')
    
    # Gestione data scadenza (se vuota diventa None)
    if expiry:
        expiry_date = datetime.strptime(expiry, '%Y-%m-%d').date()
    else:
        expiry_date = None

    # Creazione Lotto
    new_batch = Batch(
        product_id=product.id,
        quantity_initial=qty,
        quantity_current=qty,
        expiry_date=expiry_date,
        created_by=current_user.username # Salviamo chi sta facendo l'operazione
    )
    db.session.add(new_batch)

    # Log operazione
    log = Log(user_id=current_user.username, product_name=product.name, action="IN", quantity_change=qty)
    db.session.add(log)

    db.session.commit()
    flash(f'Caricati {qty} {product.unit_measure} di {product.name}', 'success')
    return redirect(url_for('product_detail', id=product.id))

# Utilizzo di un Lotto (Scarico Merce)
@app.route('/use_batch/<int:batch_id>', methods=['POST'])
@login_required
def use_batch(batch_id):
    batch = Batch.query.get_or_404(batch_id)
    qty_to_use = float(request.form.get('quantity_use'))
    
    # Controllo se c'è abbastanza merce
    if qty_to_use > batch.quantity_current:
        flash('Quantità insufficiente nel lotto selezionato!', 'danger')
        return redirect(url_for('product_detail', id=batch.product_id))

    # Aggiornamento quantità residua
    batch.quantity_current -= qty_to_use

    # Log operazione
    log = Log(user_id=current_user.username, product_name=batch.product.name, action="OUT", quantity_change=qty_to_use)
    db.session.add(log)

    db.session.commit()
    flash('Scarico effettuato con successo.', 'success')
    return redirect(url_for('product_detail', id=batch.product_id))

# Gestione Utenti (Solo per Admin)
@app.route('/admin/users', methods=['GET', 'POST'])
@login_required
def admin_users():
    # Protezione: se non sei admin, vieni cacciato
    if current_user.role != 'admin':
        flash('Accesso negato', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        # Logica creazione utente
        if action == 'create':
            username = request.form.get('username')
            password = request.form.get('password')
            role = request.form.get('role')
            if User.query.filter_by(username=username).first():
                flash('Username già in uso', 'danger')
            else:
                new_user = User(username=username, password_hash=generate_password_hash(password), role=role)
                db.session.add(new_user)
                db.session.commit()
                flash('Utente creato', 'success')
        # Logica eliminazione utente
        elif action == 'delete':
            user_id = request.form.get('user_id')
            User.query.filter_by(id=user_id).delete()
            db.session.commit()
            flash('Utente eliminato', 'warning')

    users = User.query.all()
    return render_template('admin_users.html', users=users)

# Export Dati in CSV
@app.route('/export_csv')
@login_required
def export_csv():
    products = Product.query.all()

    # Crea un file CSV in memoria (senza salvarlo su disco)
    output = io.StringIO()
    writer = csv.writer(output)

    # Scrittura intestazioni
    writer.writerow(['Codice', 'Prodotto', 'Marca', 'Fornitore', 'Giacenza Totale', 'Unita', 'Prezzo Unitario', 'Valore Totale'])

    # Scrittura righe prodotti
    for p in products:
        valore = p.total_quantity * p.unit_price
        writer.writerow([p.barcode, p.name, p.brand, p.supplier, p.total_quantity, p.unit_measure, p.unit_price, valore])

    output.seek(0)

    # Invio file al browser per il download
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'inventario_{datetime.now().strftime("%Y%m%d")}.csv'
    )

# --- AVVIO APP ---
if __name__ == '__main__':
    create_admin() # Assicura che l'admin esista
    # Avvio del server
    # host='0.0.0.0' rende il sito visibile nella rete locale
    # ssl_context='adhoc' genera l'HTTPS per far funzionare la fotocamera
    app.run(host='0.0.0.0', port=8000, debug=True, ssl_context='adhoc')