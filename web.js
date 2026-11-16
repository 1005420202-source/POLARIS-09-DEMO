'use strict';

const endDate = new Date();
const startDate = new Date(endDate.getTime() - 90 * 86400000);
$('start').value = startDate.toISOString().slice(0, 10);
$('end').value = endDate.toISOString().slice(0, 10);
const controls = Array.from(document.querySelectorAll('[data-job]'));
const output = $('output');
const units = {precipitation_chirps: 'mm', gpm_imerg: 'mm',
               smap_soil_moisture: 'm³/m³', gldas_soil_moisture: 'kg/m²',
               nasadem_elevation: 'm', nasadem_slope: '°', jrc_occurrence: '%'};
let busy = false;

async function api(path, body) {
    const options = body === undefined ? {} : {method:'POST', body};
    if (body && !(body instanceof FormData)) {
        options.headers = {'Content-Type': 'application/json'};
        options.body = JSON.stringify(body);
    }
    const response = await fetch('/api/' + path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'No se pudo conectar con el motor de procesamiento.');
    return data;
}

function legend(item) {
    const visual = item.legend;
    const result = document.createElement('div');
    if (!visual) return result;
    if (visual.classes) {
        for (const [code, [label, color]] of Object.entries(visual.classes)) {
            const row = document.createElement('div');
            const swatch = document.createElement('span');
            swatch.className = 'swatch';
            swatch.style.backgroundColor = color;
            row.className = 'legend-item';
            row.append(swatch, code + ' · ' + label);
            result.append(row);
        }
    } else if (visual.palette) {
        const bar = document.createElement('div');
        bar.style.cssText = 'height:12px;border:1px solid #98a9b5;border-radius:3px;max-width:420px';
        bar.style.background = 'linear-gradient(to right,' + visual.palette.map(color => '#' + color).join(',') + ')';
        result.append(bar, visual.min + ' a ' + visual.max + ' ' + (units[item.key] || '(índice sin unidad)'));
    }
    return result;
}

function show(data) {
    $('maps').replaceChildren();
    for (const item of data.maps || []) {
        const section = document.createElement('div');
        const image = document.createElement('img');
        image.className = 'photo';
        image.src = item.path;
        image.alt = item.title;
        const title = document.createElement('p');
        title.textContent = item.title + (item.scale_note ? ' · ' + item.scale_note : '');
        section.append(title, image, legend(item));
        $('maps').append(section);
    }
    if (data.path) {
        const link = document.createElement('a');
        link.href = data.path;
        link.textContent = 'Descargar informe PDF';
        $('maps').append(link);
    }
    if (data.downloads?.length) {
        const downloads = document.createElement('div');
        downloads.className = 'maptools';
        for (const file of data.downloads) {
            const link = document.createElement('a');
            link.href = file.path;
            link.download = '';
            link.textContent = file.label;
            downloads.append(link);
        }
        $('maps').append(downloads);
    }
    $('values').textContent = data.answer ? data.provider + '\n\n' + data.answer
        : (data.result ? JSON.stringify(data.result, null, 2) : '');
    $('values').hidden = !$('values').textContent;
}

function bind(id, path, getBody) {
    $(id).addEventListener('click', async () => {
        if (busy) return;
        busy = true;
        controls.forEach(button => button.disabled = true);
        $('worker').textContent = 'Procesando en el servidor…';
        output.scrollIntoView({block:'nearest'});
        try {
            const data = await api(path, getBody());
            show(data);
            $('worker').textContent = path === 'project' ? 'Conexión a Earth Engine verificada.' : 'Proceso terminado.';
        } catch (error) {
            show({});
            $('worker').textContent = error.message;
        } finally {
            busy = false;
            controls.forEach(button => button.disabled = false);
        }
    });
}

function selectedArea() {
    if (!aoiLayer) throw new Error('Dibuja o carga un polígono antes de ejecutar el proceso.');
    return aoiLayer.toGeoJSON();
}

bind('connect', 'project', () => ({}));
bind('run-gis', 'gis', () => ({
    aoi:selectedArea(), module:$('product').value, start:$('start').value, end:$('end').value
}));
bind('run-sim','simulation', () => ({
    aoi:selectedArea(), soil:$('soil').value, rain:$('rain').value, initial:$('initial').value
}));
bind('run-aqua','aquafinder', () => ({
    aoi: selectedArea(),
    ...Object.fromEntries(Array.from(document.querySelectorAll('[data-aqua]'),
        input => [input.dataset.aqua, input.value]))
}));
bind('run-uav','uav', () => {
    const file = $('uav').files[0];
    if (!file) throw new Error('Selecciona una imagen UAV.');
    const form = new FormData();
    form.append('image', file);
    return form;
});
bind('run-ai','assistant', () => {
    const form = new FormData();
    form.append('question',$('question').value);
    if ($('photo').files[0]) form.append('image',$('photo').files[0]);
    return form;
});
bind('run-report','report', () => ({}));
bind('run-blender','blender', () => ({aoi:selectedArea()}));

api('status').then(config => {
    for (const [value, label] of Object.entries(config.products)) {
        $('product').add(new Option(label,value));
    }
    $('gee-state').textContent = config.gee ? 'Proyecto configurado; puedes probar la conexión.'
        : 'Falta configurar Earth Engine en el servidor. Simulación, UAV y PDF siguen disponibles.';
    $('ai-state').textContent = config.ai ? 'Modelo IA configurado.'
        : 'Guía por reglas disponible. El diagnóstico visual requiere que el servidor configure un modelo IA.';
    $('status').textContent = 'Motor web conectado';
    $('worker').textContent = 'Selecciona un proceso para empezar.';
}).catch(error => {
    $('status').textContent = 'Motor web desconectado';
    $('worker').textContent = error.message;
});
