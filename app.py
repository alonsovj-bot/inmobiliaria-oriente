import os, json, re, subprocess, uuid, shutil
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template

app = Flask(__name__)

BASE     = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE, 'static', 'data')
PDF_DIR  = os.path.join(BASE, 'static', 'uploads', 'pdfs')
MAP_DIR  = os.path.join(BASE, 'static', 'uploads', 'mapas')

for d in [DATA_DIR, PDF_DIR, MAP_DIR]:
    os.makedirs(d, exist_ok=True)

FILES = {
    'props':   os.path.join(DATA_DIR, 'propiedades.json'),
    'clients': os.path.join(DATA_DIR, 'clientes.json'),
    'ventas':  os.path.join(DATA_DIR, 'ventas.json'),
    'cfg':     os.path.join(DATA_DIR, 'config.json'),
    'order':   os.path.join(DATA_DIR, 'order.json'),
}

def read(key):
    try:
        with open(FILES[key]) as f: return json.load(f)
    except: return []

def read_dict(key):
    try:
        with open(FILES[key]) as f: return json.load(f)
    except: return {}

def write(key, data):
    with open(FILES[key], 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ── PDF PARSING ──────────────────────────────────────────────────────────────
JUNK = re.compile(r'imagen|ilustrativa|remodelacion|asignada|proceso|ustrativa', re.I)

def parse_pdf(path):
    result = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True)
    raw = result.stdout
    pages = raw.split('\f')
    fichas = []

    for page in pages:
        if '•' not in page and '$' not in page:
            continue
        lines = [l.strip() for l in page.splitlines() if l.strip()]
        precio = 0; direccion = ''; fraccionamiento = ''

        for j, line in enumerate(lines):
            m1 = re.match(r'^\$([\d,]+)\s+(.+#[\d\-]+)', line)
            if m1:
                precio = int(m1.group(1).replace(',',''))
                direccion = m1.group(2).strip()
                for k in range(j+1, min(j+4, len(lines))):
                    c = re.sub(r'Imagen.*|ustrativa.*', '', lines[k], flags=re.I).strip()
                    if c and not JUNK.search(c) and '$' not in c and '#' not in c and len(c) > 3:
                        fraccionamiento = c; break
                break
            m2 = re.match(r'^\$([\d,]+)$', line)
            if m2:
                precio = int(m2.group(1).replace(',',''))
                for k in range(j+1, min(j+5, len(lines))):
                    if not direccion and re.search(r'#[\d\-]+', lines[k]) and not JUNK.search(lines[k]):
                        direccion = lines[k].strip()
                    elif direccion and not fraccionamiento:
                        c = re.sub(r'Imagen.*|ustrativa.*|\s{3,}.*', '', lines[k], flags=re.I).strip()
                        if c and not JUNK.search(c) and '$' not in c and '#' not in c and len(c) > 3:
                            fraccionamiento = c; break
                break

        if not direccion and not precio:
            continue

        chars = []; cur = ''
        for line in lines:
            if line.startswith('•'):
                if cur: chars.append(cur.strip())
                cur = line.lstrip('• ').strip()
            elif cur and not line.startswith('$') and not JUNK.search(line) and '#' not in line:
                cur += ' ' + line
            else:
                if cur: chars.append(cur.strip()); cur = ''
        if cur: chars.append(cur.strip())

        fid = f"{direccion.lower().strip()}|{fraccionamiento.lower().strip()}"
        fichas.append({
            'id': fid,
            'direccion': direccion,
            'fraccionamiento': fraccionamiento,
            'precio': precio,
            'caracteristicas': chars,
            'fecha': datetime.now().strftime('%d/%m/%Y'),
        })
    return fichas

# ── ROUTES ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    with open(os.path.join(BASE, 'index.html')) as f:
    return f.read()


@app.route('/api/upload-pdf', methods=['POST'])
def upload_pdf():
    file = request.files.get('pdf')
    if not file: return jsonify({'error': 'No PDF'}), 400

    path = os.path.join(PDF_DIR, 'inventario.pdf')
    file.save(path)

    nuevas_fichas = parse_pdf(path)
    existing = read('props')
    existing_map = {p['id']: p for p in existing}

    nuevas = []; actualizadas = []; repetidas = []
    for f in nuevas_fichas:
        prev = existing_map.get(f['id'])
        if not prev:
            f['_estado'] = 'nueva'; nuevas.append(f)
        elif str(prev['precio']) != str(f['precio']):
            f['_estado'] = 'precio_cambio'; f['_precioAnterior'] = prev['precio']
            actualizadas.append(f)
        else:
            f['_estado'] = 'repetida'; repetidas.append(f)

    fps_pdf = {f['id'] for f in nuevas_fichas}
    ventas_ids = {v['prop_id'] for v in read('ventas')}
    vendidas = [
        {**p, '_estado': 'vendida'}
        for p in existing
        if p['id'] not in fps_pdf and p.get('_estado') != 'vendida' and p['id'] not in ventas_ids
    ]

    final_map = {}
    for f in [*nuevas, *actualizadas, *vendidas, *repetidas]:
        if f['id'] not in final_map:
            final_map[f['id']] = f

    # Preserve order
    order = read('order') or []
    ordered = []
    seen = set()
    for oid in order:
        if oid in final_map:
            ordered.append(final_map[oid]); seen.add(oid)
    for fid, f in final_map.items():
        if fid not in seen:
            ordered.insert(0, f)

    write('props', ordered)
    write('order', [f['id'] for f in ordered])

    return jsonify({
        'total': len(ordered),
        'nuevas': len(nuevas),
        'actualizadas': len(actualizadas),
        'vendidas': len(vendidas),
        'repetidas': len(repetidas),
    })

@app.route('/api/propiedades')
def get_props():
    return jsonify(read('props'))

@app.route('/api/propiedades/order', methods=['POST'])
def save_order():
    order = request.json.get('order', [])
    props = read('props')
    props_map = {p['id']: p for p in props}
    ordered = [props_map[oid] for oid in order if oid in props_map]
    write('props', ordered)
    write('order', order)
    return jsonify({'ok': True})

@app.route('/api/propiedades/<path:prop_id>/vender', methods=['POST'])
def vender(prop_id):
    data = request.json or {}
    vendedor = data.get('vendedor', 'yo')  # 'yo' | 'compañero'
    props = read('props')
    for p in props:
        if p['id'] == prop_id:
            p['_estado'] = 'vendida'
            break
    write('props', props)

    ventas = read('ventas')
    ventas.append({
        'prop_id': prop_id,
        'vendedor': vendedor,
        'fecha': datetime.now().strftime('%d/%m/%Y %H:%M'),
    })
    write('ventas', ventas)
    return jsonify({'ok': True})

@app.route('/api/propiedades/<path:prop_id>', methods=['DELETE'])
def delete_prop(prop_id):
    props = [p for p in read('props') if p['id'] != prop_id]
    write('props', props)
    return jsonify({'ok': True})

@app.route('/api/upload-mapa', methods=['POST'])
def upload_mapa():
    file = request.files.get('mapa')
    fracc = request.form.get('fraccionamiento', '')
    if not file or not fracc: return jsonify({'error': 'Faltan datos'}), 400
    ext = os.path.splitext(file.filename)[1] or '.jpg'
    safe = re.sub(r'[^a-z0-9]', '_', fracc.lower()) + ext
    file.save(os.path.join(MAP_DIR, safe))
    return jsonify({'ok': True, 'filename': safe})

@app.route('/api/mapa/<fracc>')
def get_mapa(fracc):
    safe = re.sub(r'[^a-z0-9]', '_', fracc.lower())
    for ext in ['.jpg','.jpeg','.png','.webp']:
        fname = safe + ext
        fpath = os.path.join(MAP_DIR, fname)
        if os.path.exists(fpath):
            return send_from_directory(MAP_DIR, fname)
    return jsonify({'error': 'No encontrado'}), 404

@app.route('/api/clientes', methods=['GET'])
def get_clients():
    return jsonify(read('clients'))

@app.route('/api/clientes', methods=['POST'])
def add_client():
    data = request.json
    data['id'] = str(uuid.uuid4())
    clients = read('clients')
    clients.insert(0, data)
    write('clients', clients)
    return jsonify(data)

@app.route('/api/clientes/<cid>', methods=['PUT'])
def update_client(cid):
    data = request.json
    clients = [data if c['id']==cid else c for c in read('clients')]
    write('clients', clients)
    return jsonify({'ok': True})

@app.route('/api/clientes/<cid>', methods=['DELETE'])
def delete_client(cid):
    write('clients', [c for c in read('clients') if c['id'] != cid])
    return jsonify({'ok': True})

@app.route('/api/config', methods=['GET'])
def get_cfg():
    return jsonify(read_dict('cfg'))

@app.route('/api/config', methods=['POST'])
def save_cfg():
    write('cfg', request.json)
    return jsonify({'ok': True})

@app.route('/api/ventas')
def get_ventas():
    return jsonify(read('ventas'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
