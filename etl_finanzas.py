import pandas as pd
import mysql.connector
import os

def ejecutar_etl_gencana():
    print("Iniciando proceso ETL para GENCANA...")
    
    # 1. Extracción (Extract)[cite: 2]
    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="gencanasys_db"
    )
    
    # Consulta 1: Contabilidad General
    query_transacciones = """
        SELECT t.ID_Transaccion, t.Tipo, t.Monto, t.Fecha, t.Categoria, 
               COALESCE(e.Nombre_Evento, 'Gasto Administrativo General') AS Evento
        FROM Transacciones_Financieras t
        LEFT JOIN Eventos e ON t.ID_Evento = e.ID_Evento
    """
    df_transacciones = pd.read_sql(query_transacciones, db)
    
    # Consulta 2: Estados de Cuenta Individualizados
    query_cuotas = """
        SELECT c.ID_Cuota, p.Nombres, p.Numero_Identificacion, e.Nombre_Evento,
               c.Monto_Total, c.Abono, c.Saldo_Pendiente, c.Estado
        FROM Cuotas_Participantes c
        JOIN Participantes p ON c.ID_Participante = p.ID_Participante
        JOIN Eventos e ON c.ID_Evento = e.ID_Evento
    """
    df_cuotas = pd.read_sql(query_cuotas, db)
    db.close()

    # 2. Transformación (Transform)[cite: 2]
    # Limpieza y formateo de fechas para el análisis temporal en Power BI
    if not df_transacciones.empty:
        df_transacciones['Fecha'] = pd.to_datetime(df_transacciones['Fecha'])
        df_transacciones['Mes'] = df_transacciones['Fecha'].dt.month_name()
        df_transacciones['Año'] = df_transacciones['Fecha'].dt.year

    # 3. Carga (Load)[cite: 2]
    os.makedirs("data_warehouse", exist_ok=True)
    df_transacciones.to_csv('data_warehouse/fact_transacciones.csv', index=False)
    df_cuotas.to_csv('data_warehouse/fact_estados_cuenta.csv', index=False)
    
    print("✅ ETL completado con éxito. Archivos listos en la carpeta 'data_warehouse'.")

if __name__ == "__main__":
    ejecutar_etl_gencana()