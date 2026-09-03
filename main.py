from fastapi.responses import FileResponse
from datetime import datetime
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import date
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

# Permitir conexión desde el frontend HTML
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ParticipanteCreate(BaseModel):
    nombres: str
    numero_identificacion: str
    fecha_nacimiento: date
    genero: str
    telefono: str
    fecha_ingreso: date

class AsistenciaScan(BaseModel):
    evento_id: int
    qr_data: str = None
    numero_identificacion: str = None

def get_db():
    return mysql.connector.connect(
        host="mysql.railway.internal",
        port="3306",
        user="root",
        password="AmhdlxnvdAEntWOnPIKlDnoAfrxElGxq",
        database="railway"
    )
@app.post("/participantes/", response_model=dict)
def crear_participante(participante: ParticipanteCreate):
    db = get_db()
    cursor = db.cursor()
    # Insertamos temporalmente con un valor genérico
    query = """INSERT INTO Participantes (Nombres, Numero_Identificacion, Fecha_Nacimiento, Genero, Telefono, Fecha_Ingreso) 
               VALUES (%s, %s, %s, %s, %s, %s)"""
    valores = (participante.nombres, "TEMP", participante.fecha_nacimiento, participante.genero, participante.telefono, participante.fecha_ingreso)
    
    try:
        cursor.execute(query, valores)
        participante_id = cursor.lastrowid
        
        # Generar identificador secuencial GENCANA empezando en gnc300
        numero_secuencial = 299 + participante_id
        numero_gencana = f"gnc{numero_secuencial}"
        
        qr_data = f"GENCANA-ID-{participante_id}"
        img = qrcode.make(qr_data)
        os.makedirs("qrcodes", exist_ok=True)
        img.save(f"qrcodes/participante_{participante_id}.png")
        
        # Actualizar con el número oficial generado y el QR
        cursor.execute("UPDATE Participantes SET Numero_Identificacion = %s, Codigo_QR = %s WHERE ID_Participante = %s", 
                       (numero_gencana, qr_data, participante_id))
        db.commit()
        
        return {"mensaje": "Participante registrado", "id": participante_id, "numero_asignado": numero_gencana}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.post("/asistencia/", response_model=dict)
def registrar_asistencia(datos: AsistenciaScan):
    db = get_db()
    # EL CAMBIO ESTÁ AQUÍ: buffered=True soluciona el error de resultados sin leer
    cursor = db.cursor(buffered=True) 
    try:
        # 1. Identificar al participante y obtener su nombre
        if datos.qr_data:
            cursor.execute("SELECT ID_Participante, Nombres FROM Participantes WHERE Codigo_QR = %s", (datos.qr_data,))
        elif datos.numero_identificacion:
            cursor.execute("SELECT ID_Participante, Nombres FROM Participantes WHERE Numero_Identificacion = %s", (datos.numero_identificacion,))
        else:
            raise HTTPException(status_code=400, detail="Envíe QR o Cédula")

        resultado = cursor.fetchone()
        if not resultado:
            raise HTTPException(status_code=404, detail="Participante no encontrado en el sistema.")
            
        id_participante, nombre_participante = resultado[0], resultado[1]

        # 2. Validar si el usuario realmente está inscrito en este evento
        cursor.execute("SELECT ID_Cuota FROM Cuotas_Participantes WHERE ID_Participante = %s AND ID_Evento = %s", (id_participante, datos.evento_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail=f"❌ {nombre_participante} no está inscrito en este evento.")

        # 3. Validar si ya había registrado su asistencia antes
        cursor.execute("SELECT ID_Asistencia FROM Asistencia WHERE ID_Participante = %s AND ID_Evento = %s", (id_participante, datos.evento_id))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail=f"⚠️ La asistencia de {nombre_participante} ya estaba confirmada.")

        # 4. Registrar la asistencia si pasó todos los filtros
        cursor.execute("INSERT INTO Asistencia (ID_Participante, ID_Evento, Confirmacion_Asistencia) VALUES (%s, %s, %s)",
                       (id_participante, datos.evento_id, True))
        db.commit()
        return {
            "mensaje": f"Asistencia confirmada para {nombre_participante}",
            "id_participante": id_participante
        }
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# Nueva ruta para obtener la lista de inscritos de un evento específico
@app.get("/eventos/{evento_id}/inscritos", response_model=list)
def obtener_inscritos_evento(evento_id: int):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        # Busca a los inscritos y revisa si ya tienen asistencia registrada
        query = """
            SELECT p.ID_Participante, p.Nombres, p.Numero_Identificacion,
                   (SELECT COUNT(*) FROM Asistencia a WHERE a.ID_Participante = p.ID_Participante AND a.ID_Evento = c.ID_Evento) as Asistio
            FROM Cuotas_Participantes c
            JOIN Participantes p ON c.ID_Participante = p.ID_Participante
            WHERE c.ID_Evento = %s
            ORDER BY p.Nombres ASC
        """
        cursor.execute(query, (evento_id,))
        return cursor.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ---------------- NUEVOS MODELOS FINANCIEROS ----------------
class TransaccionCreate(BaseModel):
    tipo: str # 'Ingreso' o 'Egreso'[cite: 2]
    monto: float
    categoria: str
    evento_id: int = None # Permite nulo para gastos administrativos generales[cite: 2]

class CuotaAbono(BaseModel):
    participante_id: int
    evento_id: int
    monto_abono: float
    monto_total_evento: float = 50.00 # Cuota base del evento como ejemplo

# ---------------- RUTAS FINANCIERAS ----------------
# RF-04 y RF-05: Registro general de ingresos y egresos[cite: 2]
@app.post("/finanzas/transaccion/", response_model=dict)
def registrar_transaccion(transaccion: TransaccionCreate):
    db = get_db()
    cursor = db.cursor()
    query = """INSERT INTO Transacciones_Financieras (Tipo, Monto, Categoria, ID_Evento) 
               VALUES (%s, %s, %s, %s)"""
    valores = (transaccion.tipo, transaccion.monto, transaccion.categoria, transaccion.evento_id)
    try:
        cursor.execute(query, valores)
        db.commit()
        return {"mensaje": f"{transaccion.tipo} registrado exitosamente por ${transaccion.monto}"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# Módulo Analítico: Control individualizado de estados de cuenta[cite: 1]
@app.post("/finanzas/abono/", response_model=dict)
def registrar_abono(abono: CuotaAbono):
    db = get_db()
    cursor = db.cursor()
    
    # 1. Verificar si ya existe un registro de cuota para este participante
    cursor.execute("SELECT ID_Cuota, Abono FROM Cuotas_Participantes WHERE ID_Participante = %s AND ID_Evento = %s", 
                   (abono.participante_id, abono.evento_id))
    resultado = cursor.fetchone()
    
    try:
        if resultado:
            # Sumar el nuevo abono al existente
            nuevo_abono = float(resultado[1]) + abono.monto_abono
            cursor.execute("UPDATE Cuotas_Participantes SET Abono = %s WHERE ID_Cuota = %s", 
                           (nuevo_abono, resultado[0]))
        else:
            # Crear el registro de la cuota por primera vez
            cursor.execute("""INSERT INTO Cuotas_Participantes (ID_Participante, ID_Evento, Monto_Total, Abono) 
                              VALUES (%s, %s, %s, %s)""", 
                           (abono.participante_id, abono.evento_id, abono.monto_total_evento, abono.monto_abono))
        
        # 2. Registrar automáticamente este abono como un 'Ingreso' en la contabilidad general[cite: 2]
        cursor.execute("""INSERT INTO Transacciones_Financieras (Tipo, Monto, Categoria, ID_Evento) 
                          VALUES ('Ingreso', %s, 'Cuota Participante', %s)""", 
                       (abono.monto_abono, abono.evento_id))
                       
        db.commit()
        return {"mensaje": f"Abono de ${abono.monto_abono} procesado. Contabilidad general actualizada."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ---------------- AUTENTICACIÓN Y PERFIL ----------------

@app.get("/participantes/buscar/{numero_id}")
def buscar_participante(numero_id: str):
    db = get_db()
    cursor = db.cursor()
    try:
        # Extraemos Fecha_Nacimiento y Fecha_Ingreso
        cursor.execute("SELECT ID_Participante, Nombres, Fecha_Nacimiento, Fecha_Ingreso, Foto_URL FROM Participantes WHERE Numero_Identificacion = %s", (numero_id,))
        resultado = cursor.fetchone()
        
        if not resultado:
            raise HTTPException(status_code=404, detail="Participante no encontrado")
            
        id_participante, nombres, fecha_nacimiento, fecha_ingreso, foto_url = resultado
        
        # Sistema automatizado de alertas (Cumpleaños y Aniversario)[cite: 1]
        hoy = datetime.now().date()
        es_cumpleanos = (fecha_nacimiento.month == hoy.month and fecha_nacimiento.day == hoy.day)
        es_aniversario = (fecha_ingreso.month == hoy.month and fecha_ingreso.day == hoy.day)
        
        return {
            "id_participante": id_participante,
            "nombres": nombres,
            "es_cumpleanos": es_cumpleanos,
            "es_aniversario": es_aniversario,
            "foto_url": foto_url
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()
# ---------------- GESTIÓN DE EVENTOS ----------------

class EventoCreate(BaseModel):
    nombre_evento: str
    tipo_evento: str = "General"
    costo: float = 0.0
    tarifas: str = ""
    fecha_inicio: str
    fecha_fin: str = None
    requisitos: str

@app.post("/admin/eventos/", response_model=dict)
def crear_evento(evento: EventoCreate):
    db = get_db()
    cursor = db.cursor()
    query = """INSERT INTO Eventos (Nombre_Evento, Tipo_Evento, Costo, Tarifas, Fecha_Inicio, Fecha_Fin, Requisitos) 
               VALUES (%s, %s, %s, %s, %s, %s, %s)"""
    valores = (evento.nombre_evento, evento.tipo_evento, evento.costo, evento.tarifas, evento.fecha_inicio, evento.fecha_fin, evento.requisitos)
    try:
        cursor.execute(query, valores)
        db.commit()
        return {"mensaje": "Evento creado exitosamente"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/eventos/", response_model=list)
def listar_eventos():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT ID_Evento, Nombre_Evento, Costo, Fecha_Inicio, Requisitos FROM Eventos ORDER BY Fecha_Inicio DESC")
        eventos = cursor.fetchall()
        return eventos
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

from fastapi.staticfiles import StaticFiles
import shutil

# Habilitar una carpeta pública para servir las imágenes subidas (perfiles y eventos)
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Ruta para eliminar eventos (Admin)
@app.delete("/admin/eventos/{evento_id}", response_model=dict)
def eliminar_evento(evento_id: int):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM Eventos WHERE ID_Evento = %s", (evento_id,))
        db.commit()
        return {"mensaje": "Evento eliminado correctamente"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# Ruta para actualizar la foto de perfil del usuario
@app.post("/participantes/{participante_id}/foto", response_model=dict)
def actualizar_foto(participante_id: int, archivo: UploadFile = File(...)):
    db = get_db()
    cursor = db.cursor()
    try:
        os.makedirs("uploads/perfiles", exist_ok=True)
        nombre_archivo = f"perfil_{participante_id}_{int(time.time())}_{archivo.filename}"
        ruta_guardado = f"uploads/perfiles/{nombre_archivo}"
        
        with open(ruta_guardado, "wb") as buffer:
            shutil.copyfileobj(archivo.file, buffer)
            
        url_foto = f"https://gencana-sistema-production.up.railway.app/uploads/perfiles/{nombre_archivo}"
        
        cursor.execute("UPDATE Participantes SET Foto_URL = %s WHERE ID_Participante = %s", (url_foto, participante_id))
        db.commit()
        return {"mensaje": "Foto de perfil actualizada con éxito", "url_foto": url_foto}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ---------------- PUBLICACIONES Y MULTIMEDIA ----------------

class PublicacionCreate(BaseModel):
    titulo: str
    descripcion: str
    tipo: str = "texto" # texto, imagen o video
    url_multimedia: str = ""

@app.post("/admin/publicaciones/", response_model=dict)
def crear_publicacion(
    titulo: str = Form(...),
    descripcion: str = Form(...),
    archivo: UploadFile = File(None)
):
    db = get_db()
    cursor = db.cursor()
    
    url_multimedia = ""
    tipo = "texto"

    # Si el administrador sube un archivo, lo guardamos con un nombre único
    if archivo and archivo.filename:
        os.makedirs("uploads", exist_ok=True)
        nombre_unico = f"{int(time.time())}_{archivo.filename}"
        ruta_guardado = f"uploads/{nombre_unico}"
        
        with open(ruta_guardado, "wb") as buffer:
            shutil.copyfileobj(archivo.file, buffer)
            
        url_multimedia = f"https://gencana-sistema-production.up.railway.app/uploads/{nombre_unico}"
        tipo = "multimedia"

    query = """INSERT INTO Publicaciones (Titulo, Descripcion, Tipo, URL_Multimedia, Fecha) 
               VALUES (%s, %s, %s, %s, NOW())"""
    try:
        cursor.execute(query, (titulo, descripcion, tipo, url_multimedia))
        db.commit()
        return {"mensaje": "Publicación compartida con éxito"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/publicaciones/", response_model=list)
def listar_publicaciones():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT ID_Publicacion, Titulo, Descripcion, Tipo, URL_Multimedia, Fecha FROM Publicaciones ORDER BY ID_Publicacion DESC")
        return cursor.fetchall()
    except Exception as e:
        # Si la tabla no existe aún, retornamos una lista vacía para evitar errores en pantalla
        return []
    finally:
        cursor.close()
        db.close()

@app.get("/qrcodes/{participante_id}")
def obtener_qr(participante_id: int):
    os.makedirs("qrcodes", exist_ok=True)
    ruta_imagen = f"qrcodes/participante_{participante_id}.png"
    
    # Si la imagen del QR no existe, la generamos automáticamente
    if not os.path.exists(ruta_imagen):
        img = qrcode.make(f"GENCANA-ID-{participante_id}")
        img.save(ruta_imagen)
        
    return FileResponse(ruta_imagen)
# Ruta para eliminar contenido multimedia (Admin)
@app.delete("/admin/publicaciones/{publicacion_id}", response_model=dict)
def eliminar_publicacion(publicacion_id: int):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM Publicaciones WHERE ID_Publicacion = %s", (publicacion_id,))
        db.commit()
        return {"mensaje": "Publicación eliminada correctamente"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ==========================================
# 1. PARTICIPANTE: Subir comprobante (Pendiente)
# ==========================================
@app.post("/inscripciones/", response_model=dict)
def inscribirse_evento(
    id_participante: int = Form(...),
    id_evento: int = Form(...),
    comprobante: UploadFile = File(...)
):
    db = get_db()
    cursor = db.cursor()
    
    # Validar si ya existe una inscripción previa para este evento
    cursor.execute("SELECT ID_Cuota FROM Cuotas_Participantes WHERE ID_Participante = %s AND ID_Evento = %s", (id_participante, id_evento))
    if cursor.fetchone():
        cursor.close()
        db.close()
        raise HTTPException(status_code=400, detail="Ya te encuentras inscrito en este evento.")
        
    # 1. Guardar la imagen física del comprobante
    os.makedirs("uploads/comprobantes", exist_ok=True)
    nombre_archivo = f"{int(time.time())}_{comprobante.filename}"
    ruta_guardado = f"uploads/comprobantes/{nombre_archivo}"
    
    with open(ruta_guardado, "wb") as buffer:
        shutil.copyfileobj(comprobante.file, buffer)
        
    url_comprobante = f"https://gencana-sistema-production.up.railway.app/uploads/comprobantes/{nombre_archivo}"
    
    cursor.execute("SELECT Costo FROM Eventos WHERE ID_Evento = %s", (id_evento,))
    evento = cursor.fetchone()
    costo = evento[0] if evento else 0.0

    query = """INSERT INTO Cuotas_Participantes 
               (ID_Participante, ID_Evento, Monto_Total, Abono, Saldo_Pendiente, Estado, Comprobante_URL) 
               VALUES (%s, %s, %s, %s, %s, 'Pendiente', %s)"""
    try:
        cursor.execute(query, (id_participante, id_evento, costo, 0, costo, url_comprobante))
        db.commit()
        return {"mensaje": "Comprobante subido. Esperando validación del administrador."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ==========================================
# 2. ADMINISTRADOR: Aprobar pago (Genera el INGRESO real)
# ==========================================
@app.post("/admin/inscripciones/{id_cuota}/aprobar")
def aprobar_inscripcion(id_cuota: int):
    db = get_db()
    cursor = db.cursor()
    
    try:
        # Obtener cuánto costó y de qué evento es
        cursor.execute("SELECT ID_Evento, Monto_Total FROM Cuotas_Participantes WHERE ID_Cuota = %s", (id_cuota,))
        cuota = cursor.fetchone()
        if not cuota:
            raise HTTPException(status_code=404, detail="Inscripción no encontrada")
            
        id_evento, monto = cuota[0], cuota[1]

        # 1. Actualizar el estado a Pagado
        cursor.execute("UPDATE Cuotas_Participantes SET Estado = 'Pagado', Abono = Monto_Total, Saldo_Pendiente = 0 WHERE ID_Cuota = %s", (id_cuota,))
        
        # 2. CREAR EL INGRESO PARA POWER BI (Aquí se vuelve dinero real)
        query_transaccion = """INSERT INTO Transacciones_Financieras 
                               (Tipo, Monto, Fecha, Categoria, ID_Evento) 
                               VALUES ('Ingreso', %s, CURDATE(), 'Inscripcion Validada', %s)"""
        cursor.execute(query_transaccion, (monto, id_evento))
        
        db.commit()
        return {"mensaje": "Pago aprobado. Dinero registrado en el balance general."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()
# ==========================================
# 2.5 ADMINISTRADOR: Rechazar pago / Anular inscripción
# ==========================================
@app.delete("/admin/inscripciones/{id_cuota}/rechazar")
def rechazar_inscripcion(id_cuota: int):
    db = get_db()
    cursor = db.cursor()
    try:
        # Al borrar la cuota pendiente, el usuario pierde su lugar temporal 
        # y el sistema le permitirá volver a inscribirse (evadiendo el bloqueo de duplicados).
        cursor.execute("DELETE FROM Cuotas_Participantes WHERE ID_Cuota = %s", (id_cuota,))
        db.commit()
        return {"mensaje": "Pago rechazado. Inscripción anulada exitosamente."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

# ==========================================
# 3. ADMINISTRADOR: Registrar Gasto Operativo
# ==========================================
@app.post("/admin/gastos/")
def registrar_gasto(
    id_evento: int = Form(...),
    monto: float = Form(...),
    categoria: str = Form(...) # Ej: Transporte, Comida, Materiales
):
    db = get_db()
    cursor = db.cursor()
    
    # Crea el EGRESO directo para que Power BI lo descuente
    query = """INSERT INTO Transacciones_Financieras 
               (Tipo, Monto, Fecha, Categoria, ID_Evento) 
               VALUES ('Egreso', %s, CURDATE(), %s, %s)"""
    try:
        cursor.execute(query, (monto, categoria, id_evento))
        db.commit()
        return {"mensaje": "Gasto operativo descontado con éxito."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        db.close()

@app.get("/admin/inscripciones/pendientes")
def obtener_pendientes():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT c.ID_Cuota, p.Nombres, e.Nombre_Evento, c.Monto_Total, c.Comprobante_URL 
        FROM Cuotas_Participantes c
        JOIN Participantes p ON c.ID_Participante = p.ID_Participante
        JOIN Eventos e ON c.ID_Evento = e.ID_Evento
        WHERE c.Estado = 'Pendiente' OR c.Estado = 'Deuda'
    """)
    columnas = [col[0] for col in cursor.description]
    pendientes = [dict(zip(columnas, row)) for row in cursor.fetchall()]
    cursor.close()
    db.close()
    return pendientes