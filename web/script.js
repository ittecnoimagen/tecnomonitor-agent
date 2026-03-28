// --- VARIABLES GLOBALES ---
let logPosition = 0;
let logInterval = null;
let statusInterval = null;
let isRunning = false;

// --- INICIALIZACIÓN ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("Iniciando TecnoMonitor GUI...");
    
    // Damos 500ms para asegurar que el bridge de Eel se sincronice con Python
    setTimeout(async () => {
        try {
            await cargarConfiguracion();
            iniciarLogReader();
            checkStatus();
            statusInterval = setInterval(checkStatus, 3000); // Refresca estado cada 3s
        } catch (err) {
            console.error("Error en el handshake de Eel:", err);
        }
    }, 500);
});

// --- LÓGICA UI ---
function toggleCard(bodyId, checkbox) {
    const el = document.getElementById(bodyId);
    if (checkbox.checked) {
        el.style.opacity = '1';
        el.style.pointerEvents = 'auto';
    } else {
        el.style.opacity = '0.5';
        el.style.pointerEvents = 'none';
    }
}

function toggleHypervisorFields() {
    const type = document.getElementById('hyper_type').value;
    const nodeContainer = document.getElementById('px_node_container');
    const hint = document.getElementById('hyper_hint');
    
    if (type === 'vmware') {
        nodeContainer.style.display = 'none';
        document.getElementById('px_node').value = ''; 
        hint.innerText = "Conexión directa a vCenter o ESXi (Puerto 443). No requiere nodo.";
    } else {
        nodeContainer.style.display = 'block';
        hint.innerText = "Requiere IP, Usuario, Password y el Nombre exacto del Nodo en el Cluster.";
    }
}

// --- BLOQUEO VISUAL DE SERVICIO ---
function bloquearBotonMonitoreo(segundos) {
    const btn = document.getElementById('btn_monitor_toggle');
    btn.disabled = true;
    btn.className = 'btn btn-secondary btn-lg'; 

    let s = segundos;
    btn.innerHTML = `<i class="fas fa-spinner fa-spin me-2"></i>Aplicando (${s}s)...`;

    const contadorVisible = setInterval(() => {
        s--;
        if (s > 0) {
            btn.innerHTML = `<i class="fas fa-spinner fa-spin me-2"></i>Aplicando (${s}s)...`;
        }
    }, 1000);

    setTimeout(async () => {
        clearInterval(contadorVisible);
        btn.disabled = false;            
        await checkStatus();             
    }, segundos * 1000); 
}

// --- CONFIGURACIÓN ---
async function cargarConfiguracion() {
    try {
        const cfg = await eel.cargar_config()();
        console.log("Datos recibidos de Python:", cfg);

        if (!cfg || Object.keys(cfg).length === 0) {
            console.warn("La configuración llegó vacía. Revisa el archivo en ProgramData.");
            return;
        }
        
        // 1. Central
        document.getElementById('hosp_id').value = cfg.hospital_id || '';
        document.getElementById('auth_token').value = cfg.auth_token || '';
        document.getElementById('central_url').value = cfg.central_url || '';
        document.getElementById('intervalo').value = cfg.interval_minutes || 5;

        // 2. Hipervisor
        if (cfg.proxmox) {
            document.getElementById('hyper_type').value = cfg.proxmox.type || 'proxmox';
            document.getElementById('px_host').value = cfg.proxmox.host || '';
            document.getElementById('px_node').value = cfg.proxmox.node || '';
            document.getElementById('px_user').value = cfg.proxmox.user || '';
            document.getElementById('px_pass').value = cfg.proxmox.pass || '';
            toggleHypervisorFields();
        }
        
        document.getElementById('enable_proxmox').checked = !!cfg.enabled_proxmox;
        toggleCard('proxmox_body', document.getElementById('enable_proxmox'));

        // 3. iDRAC
        if (cfg.idrac) {
            document.getElementById('idrac_ip').value = cfg.idrac.ip || '';
            document.getElementById('idrac_user').value = cfg.idrac.user || '';
            document.getElementById('idrac_pass').value = cfg.idrac.pass || '';
        }
        document.getElementById('enable_idrac').checked = !!cfg.enabled_idrac;
        toggleCard('idrac_body', document.getElementById('enable_idrac'));

        // 4. VMs / WS
        document.getElementById('enable_vms').checked = !!cfg.enabled_vms;
        toggleCard('vms_body', document.getElementById('enable_vms'));

        // 5. SQL Server
        if (cfg.sql) {
            document.getElementById('sql_host').value = cfg.sql.host || '';
            document.getElementById('sql_db').value = cfg.sql.db || 'ExtensaRadio';
            document.getElementById('sql_user').value = cfg.sql.user || '';
            document.getElementById('sql_pass').value = cfg.sql.pass || '';
            document.getElementById('sql_exec_day').value = cfg.sql.executions_per_day || (cfg.sql.interval_hours ? Math.floor(24/cfg.sql.interval_hours) : 3);
            // NUEVO: Cargamos la fecha de Backfill si existe
            document.getElementById('sql_start_date').value = cfg.sql.historical_start_date || '';
        }
        document.getElementById('enable_sql').checked = !!cfg.enabled_sql;
        toggleCard('sql_body', document.getElementById('enable_sql'));

        const container = document.getElementById('vms_list');
        container.innerHTML = '';
        if (cfg.vms && cfg.vms.length > 0) {
            cfg.vms.forEach(vm => agregarVM(vm));
        }
    } catch (e) {
        console.error("Fallo al cargar la configuración:", e);
    }
}

async function guardarConfiguracion() {
    const btn = document.querySelector('button[onclick="guardarConfiguracion()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Guardando...';
    btn.disabled = true;

    const vms = [];
    document.querySelectorAll('.vm-card').forEach(card => {
        vms.push({
            nombre: card.querySelector('.vm-nombre').value.trim(), 
            type: card.querySelector('.vm-type').value,
            ip: card.querySelector('.vm-ip').value.trim(),
            user: card.querySelector('.vm-user').value.trim(),
            pass: card.querySelector('.vm-pass').value,
            servicios: card.querySelector('.vm-servicios').value.trim()
        });
    });

    const config = {
        hospital_id: document.getElementById('hosp_id').value,
        auth_token: document.getElementById('auth_token').value,
        central_url: document.getElementById('central_url').value,
        interval_minutes: document.getElementById('intervalo').value,
        
        enabled_proxmox: document.getElementById('enable_proxmox').checked,
        proxmox: {
            type: document.getElementById('hyper_type').value,
            host: document.getElementById('px_host').value,
            node: document.getElementById('px_node').value,
            user: document.getElementById('px_user').value,
            pass: document.getElementById('px_pass').value
        },
        
        enabled_idrac: document.getElementById('enable_idrac').checked,
        idrac: {
            ip: document.getElementById('idrac_ip').value,
            user: document.getElementById('idrac_user').value,
            pass: document.getElementById('idrac_pass').value
        },

        enabled_sql: document.getElementById('enable_sql').checked,
        sql: {
            host: document.getElementById('sql_host').value.trim(),
            db: document.getElementById('sql_db').value.trim(),
            user: document.getElementById('sql_user').value.trim(),
            pass: document.getElementById('sql_pass').value,
            executions_per_day: parseInt(document.getElementById('sql_exec_day').value) || 3,
            // NUEVO: Guardamos la fecha de Backfill en el JSON
            historical_start_date: document.getElementById('sql_start_date').value
        },
        
        enabled_vms: document.getElementById('enable_vms').checked,
        vms: vms
    };

    try {
        const res = await eel.guardar_config(config)();
        setTimeout(() => {
            btn.disabled = false;
            btn.innerHTML = originalText;
            if (res.success) {
                bloquearBotonMonitoreo(30);
                alert("✅ Configuración Guardada.\nEl servicio se está reiniciando para aplicar los cambios.");
            } else {
                alert("❌ Error al guardar: " + res.msg);
            }
        }, 1000);
    } catch (e) {
        btn.disabled = false;
        btn.innerHTML = originalText;
        alert("Error de comunicación con el motor de Python.");
    }
}

// --- GESTIÓN DE VMs / WS / EQ (Dinámico) ---
function agregarVM(data = null) {
    const container = document.getElementById('vms_list');
    const id = Date.now();
    
    const nombreVal = data && data.nombre ? data.nombre : "";
    const alias = nombreVal ? nombreVal : "Equipo Target";
    
    const selVm = (data && data.type === 'vm') ? 'selected' : '';
    const selWs = (data && data.type === 'ws') ? 'selected' : '';
    const selEq = (data && data.type === 'eq') ? 'selected' : ''; 
    const defaultType = !data ? 'selected' : '';

    const html = `
    <div class="card p-3 mb-3 border bg-light vm-card" id="vm_${id}">
        <div class="d-flex justify-content-between mb-2">
            <h6 class="fw-bold text-primary mb-0"><i class="fas fa-desktop me-2"></i>${alias}</h6>
            <button class="btn btn-sm btn-outline-danger" onclick="document.getElementById('vm_${id}').remove()">
                <i class="fas fa-trash"></i> Quitar
            </button>
        </div>
        <div class="row g-2">
            <div class="col-md-4">
                <label class="form-label text-muted small mb-0 fw-bold">Nombre (Opcional)</label>
                <input type="text" class="form-control vm-nombre border-primary" placeholder="En blanco = Automático" value="${nombreVal}">
            </div>
            <div class="col-md-4">
                <label class="form-label text-muted small mb-0 fw-bold">Tipo</label>
                <select class="form-select vm-type">
                    <option value="vm" ${selVm || defaultType}>Máquina Virtual (VM)</option>
                    <option value="ws" ${selWs}>Workstation Física (WS)</option>
                    <option value="eq" ${selEq}>Equipo Médico (EQ)</option> </select>
            </div>
            <div class="col-md-4">
                <label class="form-label text-muted small mb-0 fw-bold">IP / Hostname</label>
                <input type="text" class="form-control vm-ip" placeholder="Ej: 192.168.1.50" value="${data ? data.ip : ''}">
            </div>
            
            <div class="col-md-4 mt-2">
                <input type="text" class="form-control vm-user" placeholder="Admin User" value="${data ? data.user : ''}">
            </div>
            <div class="col-md-4 mt-2">
                <input type="password" class="form-control vm-pass" placeholder="Password" value="${data ? data.pass : ''}" oncopy="return false" oncut="return false" onpaste="return false" autocomplete="new-password">
            </div>
            <div class="col-md-4 mt-2">
                <button class="btn btn-warning w-100 text-white shadow-sm" onclick="testVM(this)">
                    <i class="fas fa-bolt me-1"></i> Test WMI
                </button>
            </div>
            
            <div class="col-md-12 mt-2">
                <label class="form-label text-muted small mb-0 fw-bold">Servicios a monitorear</label>
                <input type="text" class="form-control vm-servicios" placeholder="Ej: MSSQLSERVER, Spooler" value="${data && data.servicios ? data.servicios : ''}">
            </div>
        </div>
    </div>`;
    
    container.insertAdjacentHTML('beforeend', html);
}

// --- TESTS ROBUSTOS ---
async function testCentral() {
    const btn = window.event ? window.event.target.closest('button') : null;
    let originalText = "Test Conexión";
    
    if (btn) {
        originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Probando...';
        btn.disabled = true;
    }
    
    const url = document.getElementById('central_url').value;
    const res = await eel.probar_conexion_central(url)();
    
    if (btn) {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
    alert(res.success ? "✅ Conexión Exitosa (Code " + res.code + ")" : "❌ Fallo: " + res.msg);
}

async function testHypervisor() {
    const type = document.getElementById('hyper_type').value;
    const btn = window.event ? window.event.target.closest('button') : null;
    let originalText = "Test Conexión";
    
    if (btn) {
        originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Probando...';
    }

    const data = {
        host: document.getElementById('px_host').value,
        node: document.getElementById('px_node').value,
        user: document.getElementById('px_user').value,
        pass: document.getElementById('px_pass').value
    };

    try {
        const result = (type === 'vmware') ? await eel.test_vmware_gui(data)() : await eel.test_proxmox_gui(data)();
        if (btn) { btn.disabled = false; btn.innerHTML = originalText; }
        alert(result.success ? "✅ ÉXITO:\n" + result.msg : "❌ ERROR:\n" + result.msg);
    } catch(e) {
        if (btn) { btn.disabled = false; btn.innerHTML = originalText; }
        alert("Error de comunicación con Python.");
    }
}

async function testIdrac() {
    const btn = window.event ? window.event.target.closest('button') : null;
    let originalText = "Test";
    
    if (btn) {
        originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
        btn.disabled = true;
    }

    const data = {
        ip: document.getElementById('idrac_ip').value,
        user: document.getElementById('idrac_user').value,
        pass: document.getElementById('idrac_pass').value
    };
    
    try {
        const res = await eel.test_idrac_gui(data)();
        if (btn) { btn.innerHTML = originalText; btn.disabled = false; }
        alert(res.success ? "✅ " + res.msg : "❌ " + res.msg);
    } catch(e) {
        if (btn) { btn.innerHTML = originalText; btn.disabled = false; }
        alert("Error de comunicación con Python.");
    }
}

async function testVM(btnElement) {
    const card = btnElement.closest('.vm-card');
    const data = {
        ip: card.querySelector('.vm-ip').value,
        user: card.querySelector('.vm-user').value,
        pass: card.querySelector('.vm-pass').value
    };
    
    const originalHtml = btnElement.innerHTML;
    btnElement.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    btnElement.disabled = true;
    
    try {
        const res = await eel.test_vm_gui(data)();
        btnElement.innerHTML = originalHtml;
        btnElement.disabled = false;
        
        if (res.success) {
            alert("✅ " + res.msg + "\n\nEl nombre se ha autocompletado en el formulario.");
            if (res.hostname) {
                card.querySelector('.vm-nombre').value = res.hostname.toUpperCase();
            }
        } else {
            alert("❌ " + res.msg);
            
            let currentName = card.querySelector('.vm-nombre').value;
            if (!currentName || currentName.trim() === "") {
                let manualName = prompt(
                    "⚠️ WMI falló o el equipo está apagado.\n\nPara evitar duplicados en el servidor cuando el equipo se encienda, ingresa manualmente el HOSTNAME real de este equipo en la red:"
                );
                
                if (manualName && manualName.trim() !== "") {
                    card.querySelector('.vm-nombre').value = manualName.trim().toUpperCase();
                }
            }
        }
    } catch(e) {
        btnElement.innerHTML = originalHtml;
        btnElement.disabled = false;
        alert("Error de comunicación con Python.");
    }
}

function testSql() {
    alert("⚠️ Para probar la conexión SQL, haz clic en 'Guardar Configuración'. El agente intentará conectar en su próximo ciclo.");
}

// NUEVO: Función para limpiar el Checkpoint y forzar Backfill
async function resetHistorial() {
    if (confirm("⚠️ ¿Estás seguro? Esto borrará la memoria del Agente y obligará a extraer todos los datos históricos desde la 'Fecha Inicio' hasta hoy.\n\nPuede tardar varias horas (1 bloque cada 5 min).")) {
        const res = await eel.reset_historial_sql()();
        if (res) {
            alert("✅ Memoria borrada. Guarda la configuración para iniciar el proceso de Backfill.");
        } else {
            alert("Memoria limpia o no había registro previo.");
        }
    }
}

// --- CONTROL DE SERVICIO ---
async function checkStatus() {
    try {
        const running = await eel.check_service_status()();
        updateStatusBadge(running);
    } catch (e) {
        // Fallo silencioso si Python no responde temporalmente
    }
}

function updateStatusBadge(running) {
    const badge = document.getElementById('service_status_badge');
    const btn = document.getElementById('btn_monitor_toggle');
    isRunning = running;

    if (running) {
        badge.innerHTML = '<span class="badge bg-success shadow fs-6"><i class="fas fa-cog fa-spin me-2"></i>EJECUTANDO (2º Plano)</span>';
    } else {
        badge.innerHTML = '<span class="badge bg-secondary shadow fs-6">DETENIDO</span>';
    }

    if (!btn.disabled) {
        if (running) {
            btn.className = 'btn btn-danger btn-lg';
            btn.innerHTML = '<i class="fas fa-stop me-2"></i>Detener Monitoreo';
        } else {
            btn.className = 'btn btn-success btn-lg';
            btn.innerHTML = '<i class="fas fa-play me-2"></i>Iniciar Monitoreo';
        }
    }
}

async function toggleMonitoreo() {
    const accion = !isRunning;
    const btn = document.getElementById('btn_monitor_toggle');
    
    btn.disabled = true;
    btn.className = 'btn btn-secondary btn-lg'; 

    try {
        const res = await eel.toggle_monitoreo(accion)();
        
        if (res && res.success === false) {
            btn.disabled = false;
            await checkStatus();
            alert("⚠️ Acción Denegada:\n" + res.msg + "\nCierra el programa y ábrelo con 'Ejecutar como administrador'.");
            return;
        }

        bloquearBotonMonitoreo(30);

    } catch(e) {
        btn.disabled = false;
        checkStatus();
    }
}

// --- LOGS ---
function iniciarLogReader() {
    if (logInterval) clearInterval(logInterval);
    
    logInterval = setInterval(async () => {
        try {
            const res = await eel.leer_log_delta(logPosition)();
            if (res && res.content) {
                const consoleBox = document.getElementById('log_console');
                
                if (logPosition === 0 && consoleBox.innerText.includes("Esperando")) {
                    consoleBox.innerText = "";
                }
                
                consoleBox.innerText += res.content;
                consoleBox.scrollTop = consoleBox.scrollHeight;
                logPosition = res.pos;
            }
        } catch(e) {
            // Falla en silencio
        }
    }, 2000);
}

async function limpiarConsola() {
    const res = await eel.limpiar_log()();
    if (res) {
        document.getElementById('log_console').innerText = "--- Log limpiado por el usuario ---";
        logPosition = 0; 
    }
}