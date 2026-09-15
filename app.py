import os
import json
import uuid
import subprocess
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='static')

# ── RUTAS DE DATOS ────────────────────────────────────────────────────────────
DATA_DIR    = os.path.join('static', 'data')
MAPAS_DIR   = os.path.join('static', 'mapas')
UPLOADS_DIR = os.path.join('static', 'uploads')

for d in [DATA_DIR, MAPAS_DIR, UPLOADS_DIR]:
    os.makedirs(d, exist_ok=True)

PROPS_FILE   = os.path.join(DATA_DIR, 'propiedades.json')
CLIENTS_FILE = os.path.join(DATA_DIR, 'clientes.json')
CONFIG_FILE  = os.path.join(DATA_DIR, 'config.json')
VENTAS_FILE  = os.path.join(DATA_DIR, 'ventas.json')
ORDER_FILE   = os.path.join(DATA_DIR, 'order.json')

# ── HELPERS JSON ──────────────────────────────────────────────────────────────
def read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default

def write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── SERVIR FRONTEND ───────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# ── PROPIEDADES ───────────────────────────────────────────────────────────────
@app.route('/api/propiedades', methods=['GET'])
def get_propiedades():
    props = read_json(PROPS_FILE, [])
    order = read_json(ORDER_FILE, [])
    if order:
        prop_map = {p['id']: p for p in props}
        ordered = [prop_map[i] for i in order if i in prop_map]
        rest = [p for p in props if p['id'] not in set(order)]
        props = ordered + rest
    return jsonify(props)

@app.route('/api/propiedades/order', methods=['POST'])
def set_order():
    data = request.get_json()
    write_json(ORDER_FILE, data.get('order', []))
    return jsonify({'ok': True})

@app.route('/api/propiedades/<prop_id>/vender', methods=['POST'])
def vender(prop_id):
    props = read_json(PROPS_FILE, [])
    ventas = read_json(VENTAS_FILE, [])
    data = request.get_json() or {}

    prop = next((p for p in props if p['id'] == prop_id), None)
    if prop:
        # Guardar en ventas permanentes
        venta = {
            'id': str(uuid.uuid4()),
            'fecha': data.get('fecha', ''),
            'direccion': prop.get('direccion', ''),
            'fraccionamiento': prop.get('fraccionamiento', ''),
            'precio': prop.get('precio', 0),
            'clienteId': data.get('clienteId', ''),
        }
        ventas.insert(0, venta)
        write_json(VENTAS_FILE, ventas)

        # Marcar como vendida en inventario
        prop['_estado'] = 'vendida'
        write_json(PROPS_FILE, props)

    return jsonify({'ok': True})

@app.route('/api/propiedades/<prop_id>', methods=['DELETE'])
def delete_prop(prop_id):
    props = read_json(PROPS_FILE, [])
    props = [p for p in props if p['id'] != prop_id]
    write_json(PROPS_FILE, props)
    return jsonify({'ok': True})

# ── UPLOAD PDF ────────────────────────────────────────────────────────────────
def parse_pdf(pdf_path):
    """Extrae fichas del PDF usando pdftotext."""
    try:
        result = subprocess.run(
            ['pdftotext', '-layout', pdf_path, '-'],
            capture_output=True, text=True, timeout=30
        )
        text = result.stdout
    except Exception as e:
        print('pdftotext error:', e)
        return []

    fichas = []
    current = {}
    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            if current.get('direccion') and current.get('precio'):
                fichas.append(current)
                current = {}
            continue

        low = line.lower()

        # Detectar precio
        import re
        precio_match = re.search(r'\$?\s*([\d,]+(?:\.\d{2})?)', line)
        if precio_match and ('precio' in low or '$' in line):
            precio_str = precio_match.group(1).replace(',', '')
            try:
                precio = float(precio_str)
                if precio > 100000:
                    current['precio'] = precio
                    continue
            except ValueError:
                pass

        # Detectar fraccionamiento
        fracs_keywords = ['riveras', 'campanario', 'zaragoza', 'san isidro',
                          'cedro', 'roma', 'creel', 'boca del río', 'sierra vista',
                          'desierto', 'fundadores', 'roble', 'alcalá', 'mirador',
                          'cañada', 'oriente', 'fraccionamiento', 'fracc']
        if any(k in low for k in fracs_keywords) and 'fraccionamiento' not in current:
            current['fraccionamiento'] = line.title()
            continue

        # Detectar dirección (calle + número)
        if re.search(r'\d{3,}', line) and 'direccion' not in current:
            current['direccion'] = line
            continue

        # Características
        if len(line) > 3 and not line.startswith('#'):
            if 'caracteristicas' not in current:
                current['caracteristicas'] = []
            current['caracteristicas'].append(line)

    if current.get('direccion') and current.get('precio'):
        fichas.append(current)

    return fichas

@app.route('/api/upload-pdf', methods=['POST'])
def upload_pdf():
    if 'pdf' not in request.files:
        return jsonify({'error': 'No file'}), 400

    f = request.files['pdf']
    filename = secure_filename(f.filename or 'inventario.pdf')
    pdf_path = os.path.join(UPLOADS_DIR, filename)
    f.save(pdf_path)

    # Parsear fichas
    new_fichas = parse_pdf(pdf_path)

    # Cargar inventario anterior para comparar
    old_props = read_json(PROPS_FILE, [])
    ventas = read_json(VENTAS_FILE, [])
    sold_ids = {v.get('propId') for v in ventas}

    # Construir nuevo inventario — reemplaza completamente
    old_map = {}
    for p in old_props:
        key = (p.get('direccion','').lower(), str(p.get('precio','')))
        old_map[key] = p

    new_props = []
    nuevas = 0
    actualizadas = 0

    for ficha in new_fichas:
        pid = str(uuid.uuid4())
        key = (ficha.get('direccion','').lower(), str(ficha.get('precio','')))
        if key in old_map:
            old = old_map[key]
            estado = 'repetida'
            if old.get('precio') != ficha.get('precio'):
                estado = 'precio_cambio'
                ficha['_precioAnterior'] = old.get('precio')
                actualizadas += 1
            else:
                pass  # sin cambio
            pid = old.get('id', pid)
        else:
            estado = 'nueva'
            nuevas += 1

        prop = {
            'id': pid,
            'direccion': ficha.get('direccion', ''),
            'fraccionamiento': ficha.get('fraccionamiento', ''),
            'precio': ficha.get('precio', 0),
            'caracteristicas': ficha.get('caracteristicas', []),
            '_estado': estado,
        }
        if '_precioAnterior' in ficha:
            prop['_precioAnterior'] = ficha['_precioAnterior']
        new_props.append(prop)

    # Las propiedades marcadas como vendidas en ventas se mantienen
    # (no se re-agregan al inventario activo)
    write_json(PROPS_FILE, new_props)
    write_json(ORDER_FILE, [p['id'] for p in new_props])

    # Limpiar PDF
    try:
        os.remove(pdf_path)
    except Exception:
        pass

    return jsonify({
        'ok': True,
        'total': len(new_props),
        'nuevas': nuevas,
        'actualizadas': actualizadas,
    })

# ── MAPAS ─────────────────────────────────────────────────────────────────────
@app.route('/api/upload-mapa', methods=['POST'])
def upload_mapa():
    if 'mapa' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['mapa']
    fracc = request.form.get('fraccionamiento', 'sin-nombre')
    safe = secure_filename(fracc.replace(' ', '_').replace('/', '-') + '.jpg')
    path = os.path.join(MAPAS_DIR, safe)
    f.save(path)
    return jsonify({'ok': True, 'path': '/static/mapas/' + safe})

@app.route('/api/mapa/<fracc>')
def get_mapa(fracc):
    safe = secure_filename(fracc.replace(' ', '_').replace('/', '-') + '.jpg')
    path = os.path.join(MAPAS_DIR, safe)
    if os.path.exists(path):
        return send_from_directory(MAPAS_DIR, safe)
    return jsonify({'error': 'Not found'}), 404

# ── CLIENTES ──────────────────────────────────────────────────────────────────
@app.route('/api/clientes', methods=['GET'])
def get_clientes():
    return jsonify(read_json(CLIENTS_FILE, []))

@app.route('/api/clientes', methods=['POST'])
def add_cliente():
    data = request.get_json()
    clients = read_json(CLIENTS_FILE, [])
    data['id'] = str(uuid.uuid4())
    clients.append(data)
    write_json(CLIENTS_FILE, clients)
    return jsonify(data)

@app.route('/api/clientes/<cid>', methods=['PUT'])
def update_cliente(cid):
    data = request.get_json()
    clients = read_json(CLIENTS_FILE, [])
    clients = [data if c['id'] == cid else c for c in clients]
    write_json(CLIENTS_FILE, clients)
    return jsonify(data)

@app.route('/api/clientes/<cid>', methods=['DELETE'])
def delete_cliente(cid):
    clients = read_json(CLIENTS_FILE, [])
    clients = [c for c in clients if c['id'] != cid]
    write_json(CLIENTS_FILE, clients)
    return jsonify({'ok': True})

# ── CONFIG ────────────────────────────────────────────────────────────────────
@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(read_json(CONFIG_FILE, {}))

@app.route('/api/config', methods=['POST'])
def set_config():
    data = request.get_json()
    write_json(CONFIG_FILE, data)
    return jsonify({'ok': True})

# ── VENTAS ────────────────────────────────────────────────────────────────────
@app.route('/api/ventas', methods=['GET'])
def get_ventas():
    return jsonify(read_json(VENTAS_FILE, []))

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
