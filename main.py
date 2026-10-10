from fastapi.responses import FileResponse
from datetime import datetime, date, timedelta
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
import shutil
import time
import qrcode
import mysql.connector
import os

os.makedirs("uploads", exist_ok=True)
os.makedirs("qrcodes", exist_ok=True)

app = FastAPI(title="GENCANA Web System API")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    return mysql.connector.connect(
        host="mysql.railway.internal",
        port="3306",
        user="root",
        password="AmhdlxnvdAEntWOnPIKlDnoAfrxElGxq",
        database="railway"
    )

# ==========================================
# MODELOS DE DATOS
# ==========================================
class ParticipanteCreate(BaseModel):
    nombres: str; numero_identificacion: str; fecha_nacimiento: date; genero: str
    correo: str; celular: str; numero_emergencia: str; parentesco_emergencia: str
    provincia: str; sede: str; rol: str; apostolado: str; fecha_ingreso: date

class AsistenciaScan(BaseModel):
    evento_id: int; qr_data: str = None; numero_identificacion: str = None

class UsuarioUpdate(BaseModel):
    nombres: str; correo: str; celular: str; rol: str; apostolado: str; nivel_acceso: str
    admin_id: int; admin_nombres: str

class EventoCreate(BaseModel):
    nombre_evento: str; tipo_evento: str = "General"; costo: float = 0.0; tarifas: str = ""
    fecha_inicio: str; fecha_fin: str = None; requisitos: str
    formulario_json: str = "{}" # <-- NUEVO: Para guardar el esqueleto del formulario
    admin_id: int; admin_nombres: str # <-- NUEVO: Para auditar quién creó el evento

# ==========================================
# MÓDULO DE AUDITORÍA
# ==========================================
def registrar_auditoria(id_usuario, nombres, accion, detalle):
    db = get_db()
    cursor = db.cursor()
    try:
        fecha_ecuador = datetime.utcnow() - timedelta(hours=5)
        fecha_formateada = fecha_ecuador.strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT INTO Auditoria (ID_Usuario, Nombres_Usuario, Accion, Detalle, Fecha) VALUES (%s, %s, %s, %s, %s)",
                       (id_usuario, nombres, accion, detalle, fecha_formateada))
        db.commit()
    except Exception as e:
        print("Error en auditoría:", e)
    finally:
        cursor.close()
        db.close()

@app.get("/admin/auditoria")
def obtener_auditoria():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM Auditoria ORDER BY Fecha DESC LIMIT 100")
    resultados = cursor.fetchall()
    cursor.close()
    db.close()
    return resultados

# ==========================================
# GESTIÓN DE PARTICIPANTES Y AUTENTICACIÓN
# ==========================================
@app.post("/participantes/", response_model=dict)
def crear_participante(participante: ParticipanteCreate):
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("SELECT ID_Participante FROM Participantes WHERE Correo = %s", (participante.correo,))
    if cursor.fetchone():
        cursor.close()
        db.close()
        raise HTTPException(status_code=400, detail="Este correo electrónico ya se encuentra registrado.")

    query = """INSERT INTO Participantes (Nombres, Numero_Identificacion, Fecha_Nacimiento, Genero, Telefono, Fecha_Ingreso, Correo, Celular, Numero_Emergencia, Parentesco_Emergencia, Provincia, Sede, Rol, Apostolado) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    valores = (participante.nombres, "TEMP", participante.fecha_nacimiento, participante.genero, participante.celular, participante.fecha_ingreso, participante.correo, participante.celular, participante.numero_emergencia, participante.parentesco_emergencia, participante.provincia, participante.sede, participante.rol, participante.apostolado)
    
    try:
        cursor.execute(query, valores)
        participante_id = cursor.lastrowid
        numero_gencana = f"gnc{299 + participante_id}"
        qr_data = f"GENCANA-ID-{participante_id}"
        
        img = qrcode.make(qr_data)
        img.save(f"qrcodes/participante_{participante_id}.png")
        
        cursor.execute("UPDATE Participantes SET Numero_Identificacion = %s, Codigo_QR = %s WHERE ID_Participante = %s", (numero_gencana, qr_data, participante_id))
        db.commit()
        return {"mensaje": "Participante registrado", "id": participante_id, "numero_asignado": numero_gencana}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/participantes/buscar/{numero_id}")
def buscar_participante(numero_id: str):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT ID_Participante, Nombres, Fecha_Nacimiento, Fecha_Ingreso, Foto_URL, Rol, Apostolado, Nivel_Acceso FROM Participantes WHERE Numero_Identificacion = %s", (numero_id,))
        resultado = cursor.fetchone()
        if not resultado: raise HTTPException(status_code=404, detail="Participante no encontrado")
            
        id_participante, nombres, fecha_nacimiento, fecha_ingreso, foto_url, rol, apostolado, nivel_acceso = resultado
        hoy = datetime.now().date()
        return {
            "id_participante": id_participante, "nombres": nombres, "es_cumpleanos": (fecha_nacimiento.month == hoy.month and fecha_nacimiento.day == hoy.day),
            "es_aniversario": (fecha_ingreso.month == hoy.month and fecha_ingreso.day == hoy.day), "foto_url": foto_url, "rol": rol, "apostolado": apostolado, "nivel_acceso": nivel_acceso or "Estándar"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/qrcodes/{participante_id}")
def obtener_qr(participante_id: int):
    ruta_imagen = f"qrcodes/participante_{participante_id}.png"
    if not os.path.exists(ruta_imagen):
        img = qrcode.make(f"GENCANA-ID-{participante_id}")
        img.save(ruta_imagen)
    return FileResponse(ruta_imagen)

@app.post("/participantes/{participante_id}/foto")
def actualizar_foto(participante_id: int, archivo: UploadFile = File(...)):
    db = get_db()
    cursor = db.cursor()
    try:
        os.makedirs("uploads/perfiles", exist_ok=True)
        nombre_archivo = f"perfil_{participante_id}_{int(time.time())}_{archivo.filename}"
        ruta_guardado = f"uploads/perfiles/{nombre_archivo}"
        with open(ruta_guardado, "wb") as buffer: shutil.copyfileobj(archivo.file, buffer)
            
        url_foto = f"https://gencana-sistema-production.up.railway.app/uploads/perfiles/{nombre_archivo}"
        cursor.execute("UPDATE Participantes SET Foto_URL = %s WHERE ID_Participante = %s", (url_foto, participante_id))
        db.commit()
        return {"mensaje": "Foto actualizada", "url_foto": url_foto}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/admin/usuarios")
def obtener_usuarios():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT ID_Participante, Nombres, Numero_Identificacion, Correo, Celular, Provincia, Sede, Rol, Apostolado, Nivel_Acceso FROM Participantes ORDER BY Nombres ASC")
    usuarios = cursor.fetchall()
    cursor.close()
    db.close()
    return usuarios

@app.put("/admin/usuarios/{id_usuario}")
def actualizar_usuario(id_usuario: int, datos: UsuarioUpdate):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            UPDATE Participantes SET Nombres=%s, Correo=%s, Celular=%s, Rol=%s, Apostolado=%s, Nivel_Acceso=%s WHERE ID_Participante=%s
        """, (datos.nombres, datos.correo, datos.celular, datos.rol, datos.apostolado, datos.nivel_acceso, id_usuario))
        db.commit()
        detalle = f"Editó el perfil de {datos.nombres} (Permiso: {datos.nivel_acceso}, Rol: {datos.rol})"
        registrar_auditoria(datos.admin_id, datos.admin_nombres, "Edición de Usuario", detalle)
        return {"mensaje": "Usuario actualizado."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ==========================================
# EVENTOS Y COMUNICACIÓN
# ==========================================
@app.post("/admin/eventos/")
def crear_evento(evento: EventoCreate):
    db = get_db()
    cursor = db.cursor()
    query = """INSERT INTO Eventos (Nombre_Evento, Tipo_Evento, Costo, Tarifas, Fecha_Inicio, Fecha_Fin, Requisitos, Formulario_JSON) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
    try:
        cursor.execute(query, (evento.nombre_evento, evento.tipo_evento, evento.costo, evento.tarifas, evento.fecha_inicio, evento.fecha_fin, evento.requisitos, evento.formulario_json))
        db.commit()
        registrar_auditoria(evento.admin_id, evento.admin_nombres, "Creación de Evento", f"Creó el evento '{evento.nombre_evento}'.")
        return {"mensaje": "Evento creado exitosamente"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/eventos/")
def listar_eventos():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT ID_Evento, Nombre_Evento, Costo, Fecha_Inicio, Requisitos, Tipo_Evento, Formulario_JSON FROM Eventos ORDER BY Fecha_Inicio DESC")
        return cursor.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.delete("/admin/eventos/{evento_id}")
def eliminar_evento(evento_id: int, admin_id: int, admin_nombres: str):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT Nombre_Evento FROM Eventos WHERE ID_Evento = %s", (evento_id,))
        ev = cursor.fetchone()
        cursor.execute("DELETE FROM Eventos WHERE ID_Evento = %s", (evento_id,))
        db.commit()
        if ev: registrar_auditoria(admin_id, admin_nombres, "Eliminación de Evento", f"Eliminó el evento '{ev[0]}'.")
        return {"mensaje": "Evento eliminado correctamente"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.post("/admin/publicaciones/")
def crear_publicacion(
    titulo: str = Form(...), descripcion: str = Form(...),
    enlace_externo: str = Form(""), # NUEVO: Video largo
    admin_id: int = Form(...), admin_nombres: str = Form(...),
    archivo: UploadFile = File(None)
):
    db = get_db()
    cursor = db.cursor()
    url_multimedia = ""
    tipo = "texto"

    if archivo and archivo.filename:
        nombre_unico = f"{int(time.time())}_{archivo.filename}"
        ruta_guardado = f"uploads/{nombre_unico}"
        with open(ruta_guardado, "wb") as buffer: shutil.copyfileobj(archivo.file, buffer)
        url_multimedia = f"https://gencana-sistema-production.up.railway.app/uploads/{nombre_unico}"
        tipo = "multimedia_local"
    elif enlace_externo:
        url_multimedia = enlace_externo
        tipo = "multimedia_externa"

    try:
        cursor.execute("INSERT INTO Publicaciones (Titulo, Descripcion, Tipo, URL_Multimedia, Fecha) VALUES (%s, %s, %s, %s, NOW())", (titulo, descripcion, tipo, url_multimedia))
        db.commit()
        registrar_auditoria(admin_id, admin_nombres, "Publicación", f"Compartió en el muro: '{titulo}'.")
        return {"mensaje": "Publicación compartida con éxito"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/publicaciones/")
def listar_publicaciones():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT ID_Publicacion, Titulo, Descripcion, Tipo, URL_Multimedia, Fecha FROM Publicaciones ORDER BY ID_Publicacion DESC")
        return cursor.fetchall()
    finally:
        cursor.close()
        db.close()

@app.delete("/admin/publicaciones/{publicacion_id}")
def eliminar_publicacion(publicacion_id: int, admin_id: int, admin_nombres: str):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM Publicaciones WHERE ID_Publicacion = %s", (publicacion_id,))
        db.commit()
        registrar_auditoria(admin_id, admin_nombres, "Eliminación de Publicación", f"Eliminó publicación ID {publicacion_id}.")
        return {"mensaje": "Publicación eliminada."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ==========================================
# ASISTENCIA Y FINANZAS
# ==========================================
@app.post("/asistencia/")
def registrar_asistencia(datos: AsistenciaScan):
    db = get_db()
    cursor = db.cursor(buffered=True) 
    try:
        if datos.qr_data: cursor.execute("SELECT ID_Participante, Nombres FROM Participantes WHERE Codigo_QR = %s", (datos.qr_data,))
        elif datos.numero_identificacion: cursor.execute("SELECT ID_Participante, Nombres FROM Participantes WHERE Numero_Identificacion = %s", (datos.numero_identificacion,))
        else: raise HTTPException(status_code=400, detail="Envíe QR o Cédula")

        resultado = cursor.fetchone()
        if not resultado: raise HTTPException(status_code=404, detail="Participante no encontrado.")
        id_participante, nombre = resultado[0], resultado[1]

        cursor.execute("SELECT ID_Cuota FROM Cuotas_Participantes WHERE ID_Participante = %s AND ID_Evento = %s", (id_participante, datos.evento_id))
        if not cursor.fetchone(): raise HTTPException(status_code=403, detail=f"❌ {nombre} no está inscrito.")

        cursor.execute("SELECT ID_Asistencia FROM Asistencia WHERE ID_Participante = %s AND ID_Evento = %s", (id_participante, datos.evento_id))
        if cursor.fetchone(): raise HTTPException(status_code=400, detail=f"⚠️ Asistencia ya confirmada para {nombre}.")

        cursor.execute("INSERT INTO Asistencia (ID_Participante, ID_Evento, Confirmacion_Asistencia) VALUES (%s, %s, %s)", (id_participante, datos.evento_id, True))
        db.commit()
        return {"mensaje": f"Asistencia confirmada: {nombre}", "id_participante": id_participante}
    except Exception as e:
        db.rollback()
        raise e if isinstance(e, HTTPException) else HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.post("/inscripciones/")
def inscribirse_evento(
    id_participante: int = Form(...), id_evento: int = Form(...),
    respuestas_json: str = Form("{}"), # NUEVO: Guarda respuestas del Google Form
    comprobante: UploadFile = File(...)
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT ID_Cuota FROM Cuotas_Participantes WHERE ID_Participante = %s AND ID_Evento = %s", (id_participante, id_evento))
    if cursor.fetchone():
        cursor.close(); db.close()
        raise HTTPException(status_code=400, detail="Ya te encuentras inscrito en este evento.")
        
    nombre_archivo = f"{int(time.time())}_{comprobante.filename}"
    with open(f"uploads/comprobantes/{nombre_archivo}", "wb") as buffer: shutil.copyfileobj(comprobante.file, buffer)
    url_comprobante = f"https://gencana-sistema-production.up.railway.app/uploads/comprobantes/{nombre_archivo}"
    
    cursor.execute("SELECT Costo FROM Eventos WHERE ID_Evento = %s", (id_evento,))
    costo = cursor.fetchone()[0]

    try:
        cursor.execute("INSERT INTO Cuotas_Participantes (ID_Participante, ID_Evento, Monto_Total, Abono, Saldo_Pendiente, Estado, Comprobante_URL) VALUES (%s, %s, %s, %s, %s, 'Pendiente', %s)", (id_participante, id_evento, costo, 0, costo, url_comprobante))
        # Guardar las respuestas del formulario de Google
        cursor.execute("INSERT INTO Respuestas_Inscripcion (ID_Participante, ID_Evento, Respuestas_JSON) VALUES (%s, %s, %s)", (id_participante, id_evento, respuestas_json))
        db.commit()
        return {"mensaje": "Comprobante y formulario enviados con éxito."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/admin/inscripciones/pendientes")
def obtener_pendientes():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.ID_Cuota, p.Nombres, e.Nombre_Evento, c.Monto_Total, c.Comprobante_URL, 
               COALESCE(r.Respuestas_JSON, '{}') as Respuestas
        FROM Cuotas_Participantes c
        JOIN Participantes p ON c.ID_Participante = p.ID_Participante
        JOIN Eventos e ON c.ID_Evento = e.ID_Evento
        LEFT JOIN Respuestas_Inscripcion r ON c.ID_Participante = r.ID_Participante AND c.ID_Evento = r.ID_Evento
        WHERE c.Estado = 'Pendiente' OR c.Estado = 'Deuda'
    """)
    res = cursor.fetchall()
    cursor.close(); db.close()
    return res

@app.post("/admin/inscripciones/{id_cuota}/aprobar")
def aprobar_inscripcion(id_cuota: int, admin_id: int = Form(...), admin_nombres: str = Form(...)):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT c.ID_Evento, c.Monto_Total, p.Nombres, e.Nombre_Evento FROM Cuotas_Participantes c JOIN Participantes p ON c.ID_Participante = p.ID_Participante JOIN Eventos e ON c.ID_Evento = e.ID_Evento WHERE c.ID_Cuota = %s", (id_cuota,))
        cuota = cursor.fetchone()
        if not cuota: raise HTTPException(status_code=404, detail="Inscripción no encontrada")
        id_evento, monto, nombre_inscrito, nombre_evento = cuota
        
        cursor.execute("UPDATE Cuotas_Participantes SET Estado = 'Pagado', Abono = Monto_Total, Saldo_Pendiente = 0 WHERE ID_Cuota = %s", (id_cuota,))
        cursor.execute("INSERT INTO Transacciones_Financieras (Tipo, Monto, Fecha, Categoria, ID_Evento) VALUES ('Ingreso', %s, CURDATE(), 'Inscripcion Validada', %s)", (monto, id_evento))
        db.commit()
        registrar_auditoria(admin_id, admin_nombres, "Aprobación de Pago", f"Aprobó ${monto} de {nombre_inscrito} para '{nombre_evento}'.")
        return {"mensaje": "Pago aprobado."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.delete("/admin/inscripciones/{id_cuota}/rechazar")
def rechazar_inscripcion(id_cuota: int, admin_id: int = Form(...), admin_nombres: str = Form(...)):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT p.Nombres, e.Nombre_Evento, c.ID_Participante, c.ID_Evento FROM Cuotas_Participantes c JOIN Participantes p ON c.ID_Participante = p.ID_Participante JOIN Eventos e ON c.ID_Evento = e.ID_Evento WHERE c.ID_Cuota = %s", (id_cuota,))
        info = cursor.fetchone()
        cursor.execute("DELETE FROM Cuotas_Participantes WHERE ID_Cuota = %s", (id_cuota,))
        if info:
            cursor.execute("DELETE FROM Respuestas_Inscripcion WHERE ID_Participante = %s AND ID_Evento = %s", (info[2], info[3]))
        db.commit()
        if info: registrar_auditoria(admin_id, admin_nombres, "Anulación", f"Rechazó comprobante de {info[0]} para '{info[1]}'.")
        return {"mensaje": "Inscripción anulada."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.post("/admin/gastos/")
def registrar_gasto(id_evento: int = Form(...), monto: float = Form(...), categoria: str = Form(...), admin_id: int = Form(...), admin_nombres: str = Form(...)):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO Transacciones_Financieras (Tipo, Monto, Fecha, Categoria, ID_Evento) VALUES ('Egreso', %s, CURDATE(), %s, %s)", (monto, categoria, id_evento))
        cursor.execute("SELECT Nombre_Evento FROM Eventos WHERE ID_Evento = %s", (id_evento,))
        ev = cursor.fetchone()
        db.commit()
        registrar_auditoria(admin_id, admin_nombres, "Egreso", f"Registró gasto de ${monto} en '{ev[0] if ev else id_evento}' ({categoria}).")
        return {"mensaje": "Gasto registrado."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/economia/eventos/{id_evento}/reporte")
def reporte_financiero_evento(id_evento: int):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT SUM(Monto) as Total_Ingresos FROM Transacciones_Financieras WHERE ID_Evento = %s AND Tipo = 'Ingreso'", (id_evento,))
        ingresos = cursor.fetchone()['Total_Ingresos'] or 0.0
        cursor.execute("SELECT SUM(Monto) as Total_Egresos FROM Transacciones_Financieras WHERE ID_Evento = %s AND Tipo = 'Egreso'", (id_evento,))
        egresos = cursor.fetchone()['Total_Egresos'] or 0.0
        cursor.execute("SELECT p.Nombres, c.Abono, c.Estado FROM Cuotas_Participantes c JOIN Participantes p ON c.ID_Participante = p.ID_Participante WHERE c.ID_Evento = %s", (id_evento,))
        inscritos = cursor.fetchall()
        cursor.execute("SELECT Categoria, Monto, Fecha FROM Transacciones_Financieras WHERE ID_Evento = %s AND Tipo = 'Egreso' ORDER BY Fecha DESC", (id_evento,))
        gastos = cursor.fetchall()
        return {"ingresos": float(ingresos), "egresos": float(egresos), "balance": float(ingresos - egresos), "inscritos": inscritos, "gastos": gastos}
    finally:
        cursor.close()
        db.close()