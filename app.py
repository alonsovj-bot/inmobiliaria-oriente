import os
import json
import uuid
import re
import subprocess
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='static')

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

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# ── PARSER PDF ────────────────────────────────────────────────────────────────
def is_skip_line(line):
    """Líneas de relleno del PDF que deben ignorarse."""
    l = line.lower().strip()
    return (
        l.startswith('imagen') or
        l.startswith('ustrativa') or
        l.startswith('en proceso') or
        l.startswith('aun no') or
        l.startswith('asignada') or
        l.startswith('remodelad') or
        l in ('en', 'enproceso', 'procesode', 'deremodelacion', 'remodelacion') or
        l == ''
    )

def parse_page(page_text):
    """Parsea una página del PDF (una ficha por página)."""
    lines = [l.strip() for l in page_text.split('\n')]
    precio = None
    precio_idx = None
    caracteristicas = []

    # 1. Encontrar el precio
    for idx, line in enumerate(lines):
        clean = line.replace('$', '').replace(',', '').replace(' ', '')
        m = re.match(r'^(\d+)$', clean)
        if m:
            val = int(m.group(1))
            if 100000 < val < 50000000:
                precio = val
                precio_idx = idx
                break

    if precio is None or precio_idx is None:
        return None

    # 2. Características (bullets antes del precio)
    current_char = None
    for line in lines[:precio_idx]:
        if is_skip_line(line):
            if current_char:
                caracteristicas.append(current_char)
                current_char = None
            continue
        if line == '•':
            if current_char is not None:
                caracteristicas.append(current_char)
            current_char = ''
        elif line.startswith('•'):
            if current_char is not None:
                caracteristicas.append(current_char)
            current_char = line[1:].strip()
        elif current_char is not None:
            current_char = (current_char + ' ' + line).strip()
    if current_char:
        caracteristicas.append(current_char)

    # 3. Dirección y fraccionamiento (después del precio)
    post = [l.strip() for l in lines[precio_idx + 1:] if l.strip()]
    direccion = None
    fraccionamiento = None

    for line in post:
        if is_skip_line(line):
            continue
        # Limpiar sufijos de "Imagen Ilustrativa:"
        line = re.sub(r'\s*[Ii]magen\s+[Ii]lustratival?:?\s*$', '', line).strip()
        if not line:
            continue
        if re.match(r'^[Uu]strativa', line, re.I):
            continue
        if direccion is None:
            direccion = line
        elif fraccionamiento is None:
            fraccionamiento = line
            break

    # Limpiar fraccionamiento de basura adicional
    if fraccionamiento:
        fraccionamiento = re.sub(r'\s*[Ii]magen.*$', '', fraccionamiento).strip()

    chars_clean = [c for c in caracteristicas if c and len(c) > 1]

    return {
        'direccion': direccion or '',
        'fraccionamiento': fraccionamiento or '',
        'precio': precio,
        'caracteristicas': chars_clean,
    }

def parse_pdf(pdf_path):
    """Extrae todas las fichas del PDF."""
    try:
        result = subprocess.run(
            ['pdftotext', pdf_path, '-'],
            capture_output=True, text=True, timeout=60
        )
        text = result.stdout
    except Exception as e:
        print('pdftotext error:', e)
        return []

    pages = text.split('\x0c')  # form feed = separador de página
    fichas = []
    for page in pages:
        if not page.strip():
            continue
        parsed = parse_page(page)
        if parsed and parsed['precio'] and parsed['direccion']:
            fichas.append(parsed)
    return fichas

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
@app.route('/api/upload-pdf', methods=['POST'])
def upload_pdf():
    if 'pdf' not in request.files:
        return jsonify({'error': 'No file'}), 400

    f = request.files['pdf']
    filename = secure_filename(f.filename or 'inventario.pdf')
    pdf_path = os.path.join(UPLOADS_DIR, filename)
    f.save(pdf_path)

    new_fichas = parse_pdf(pdf_path)
    old_props = read_json(PROPS_FILE, [])

    # Mapa de propiedades anteriores por clave
    old_map = {}
    for p in old_props:
        key = p.get('direccion', '').lower().strip()
        if key:
            old_map[key] = p

    new_props = []
    nuevas = 0
    actualizadas = 0

    for ficha in new_fichas:
        key = ficha.get('direccion', '').lower().strip()
        if key in old_map:
            old = old_map[key]
            pid = old.get('id', str(uuid.uuid4()))
            if old.get('precio') != ficha.get('precio'):
                estado = 'precio_cambio'
                actualizadas += 1
            else:
                estado = 'repetida'
        else:
            pid = str(uuid.uuid4())
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
        if estado == 'precio_cambio':
            prop['_precioAnterior'] = old_map[key].get('precio', 0)

        new_props.append(prop)

    # Reemplazar inventario completo (lógica definida: cada PDF reemplaza el anterior)
    write_json(PROPS_FILE, new_props)
    write_json(ORDER_FILE, [p['id'] for p in new_props])

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
    safe = secure_filename(fracc.replace(' ', '_').replace('/', '-')) + '.jpg'
    path = os.path.join(MAPAS_DIR, safe)
    f.save(path)
    return jsonify({'ok': True, 'path': '/static/mapas/' + safe})

@app.route('/api/mapa/<fracc>')
def get_mapa(fracc):
    safe = secure_filename(fracc.replace(' ', '_').replace('/', '-')) + '.jpg'
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
