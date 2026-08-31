import streamlit as st
import pandas as pd
import altair as alt
import io
import requests
import numpy as np 
from datetime import datetime, timedelta

# Import the new engine logic
from engine import run_optimization_logic

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(
    page_title="T1 OptiDock Solver - V24.3 Dual-Dock", 
    layout="wide", 
    page_icon="🧠",
    initial_sidebar_state="expanded"
)

# --- INYECCIÓN CSS ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    h1, h2, h3 { color: #0f172a; }
    .stButton>button {
        background-color: #2563eb; color: white; border-radius: 8px; border: none;
        padding: 0.5rem 1rem; font-weight: 600; transition: all 0.3s ease; width: 100%;
    }
    .stButton>button:hover { background-color: #1d4ed8; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
    div[data-testid="stMetric"] {
        background-color: #f8fafc; padding: 15px; border-radius: 10px;
        border: 1px solid #e2e8f0; box-shadow: 0 1px 3px 0 rgba(0,0,0,0.1);
    }
    section[data-testid="stSidebar"] { background-color: #f1f5f9; }
    </style>
""", unsafe_allow_html=True)

# --- ESTADO ---
if 'results_df' not in st.session_state: st.session_state['results_df'] = None
if 'api_key' not in st.session_state: st.session_state['api_key'] = ""

# --- GENERADOR DE ESCENARIOS ---
def generar_escenario_pais(pais_seleccionado):
    DB_PAISES = {
        "Mexico": {
            101: {'Name': 'Planta Toluca', 'Lat': 19.28, 'Lon': -99.65, 'Type': 'Plant', 'Cap': 12},
            102: {'Name': 'Planta Monterrey', 'Lat': 25.68, 'Lon': -100.31, 'Type': 'Plant', 'Cap': 10},
            201: {'Name': 'CEDI Iztapalapa', 'Lat': 19.35, 'Lon': -99.06, 'Type': 'CEDI', 'Cap': 8},
            202: {'Name': 'CEDI Puebla', 'Lat': 19.04, 'Lon': -98.20, 'Type': 'CEDI', 'Cap': 6}
        },
        "Colombia": {
            101: {'Name': 'Planta Tocancipá', 'Lat': 4.96, 'Lon': -73.94, 'Type': 'Plant', 'Cap': 10},
            102: {'Name': 'Planta Medellín', 'Lat': 6.33, 'Lon': -75.55, 'Type': 'Plant', 'Cap': 8},
            201: {'Name': 'CEDI Bogotá Sur', 'Lat': 4.59, 'Lon': -74.15, 'Type': 'CEDI', 'Cap': 6},
            202: {'Name': 'CEDI Cali', 'Lat': 3.45, 'Lon': -76.53, 'Type': 'CEDI', 'Cap': 5}
        },
        "Peru": {101: {'Name': 'Planta Lima Ate', 'Lat': -12.02, 'Lon': -76.91, 'Type': 'Plant', 'Cap': 10}, 201: {'Name': 'CEDI Arequipa', 'Lat': -16.40, 'Lon': -71.53, 'Type': 'CEDI', 'Cap': 5}},
        "Ecuador": {101: {'Name': 'Planta Quito', 'Lat': -0.18, 'Lon': -78.46, 'Type': 'Plant', 'Cap': 8}, 201: {'Name': 'CEDI Guayaquil', 'Lat': -2.18, 'Lon': -79.88, 'Type': 'CEDI', 'Cap': 6}},
        "Chile": {101: {'Name': 'Planta Renca', 'Lat': -33.40, 'Lon': -70.70, 'Type': 'Plant', 'Cap': 10}, 201: {'Name': 'CEDI Valparaiso', 'Lat': -33.04, 'Lon': -71.61, 'Type': 'CEDI', 'Cap': 5}},
        "Argentina": {101: {'Name': 'Planta Buenos Aires', 'Lat': -34.60, 'Lon': -58.38, 'Type': 'Plant', 'Cap': 12}, 201: {'Name': 'CEDI Córdoba', 'Lat': -31.42, 'Lon': -64.18, 'Type': 'CEDI', 'Cap': 6}},
        "Brasil": {101: {'Name': 'Planta Sao Paulo', 'Lat': -23.55, 'Lon': -46.63, 'Type': 'Plant', 'Cap': 15}, 201: {'Name': 'CEDI Rio', 'Lat': -22.90, 'Lon': -43.17, 'Type': 'CEDI', 'Cap': 8}},
        "Guatemala": {101: {'Name': 'Planta Guatemala', 'Lat': 14.63, 'Lon': -90.50, 'Type': 'Plant', 'Cap': 8}, 201: {'Name': 'CEDI Quetzaltenango', 'Lat': 14.83, 'Lon': -91.51, 'Type': 'CEDI', 'Cap': 4}},
        "Panama": {101: {'Name': 'Planta Panamá', 'Lat': 9.08, 'Lon': -79.41, 'Type': 'Plant', 'Cap': 6}, 201: {'Name': 'CEDI Colón', 'Lat': 9.35, 'Lon': -79.90, 'Type': 'CEDI', 'Cap': 4}}
    }
    
    nodes = DB_PAISES.get(pais_seleccionado, DB_PAISES["Mexico"])

    orders = []
    keys = list(nodes.keys())
    
    for i in range(1, 21):  # 20 orders for simplicity
        orig_id, dest_id = np.random.choice(keys), np.random.choice(keys)
        while dest_id == orig_id: dest_id = np.random.choice(keys)
        orig, dest = nodes[orig_id], nodes[dest_id]
        
        orders.append({
            'ID': f"PED-{2026000+i}", 
            'Origen_Lat': orig['Lat'], 'Origen_Lon': orig['Lon'],
            'Destino_Lat': dest['Lat'], 'Destino_Lon': dest['Lon'],
            'Tiempo_Carga_min': 45, 'Tiempo_Descarga_min': 45,
            'Skills_Requeridos': np.random.choice(['general', 'seco', 'refrigerado'], p=[0.5, 0.3, 0.2]), 
        })
    
    muelle_config = []
    for nid, data in nodes.items():
        is_plant = data['Type'] == 'Plant'
        for d in range(1, 3):
            if d == 1: skill = 'general'
            elif d == 2: skill = 'refrigerado'
            
            # Use columns expected by V24.3 engine
            muelle_config.append({
                'Nombre_Nodo': data['Name'],
                'Muelle_ID': f"M{d}_{nid}", 
                'Latitud': data['Lat'],
                'Longitud': data['Lon'],
                'Skill_Soportado': skill,
                'Inicio': "06:00",
                'Fin': "22:00",
                'Min_Operacion': 60
            })
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as w:
        pd.DataFrame(orders).to_excel(w, sheet_name='Pedidos', index=False)
        pd.DataFrame(muelle_config).to_excel(w, sheet_name='Config_Muelles', index=False)
    return output.getvalue()

def smart_load(f):
    try:
        xl = pd.ExcelFile(f)
        return pd.read_excel(f, 'Pedidos'), pd.read_excel(f, 'Config_Muelles')
    except: return pd.DataFrame(), pd.DataFrame()

# --- SIDEBAR MEJORADO (Logo CEDI/Muelle) ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2942/2942544.png", width=60) 
    st.title("T1 OptiDock")
    st.caption("Algorithm: Dual-Dock CP-SAT (V24.3)")
    
    st.markdown("### 🛠️ Configuración")
    
    with st.expander("📍 Simulación de Datos"):
        st.write("Genera datos compatibles con V24.3:")
        paises = ["Mexico", "Colombia", "Brasil", "Argentina", "Chile", "Peru", "Ecuador", "Panama", "Guatemala"]
        pais = st.selectbox("Seleccionar País", paises)
        if st.button("🎲 Generar Escenario", use_container_width=True):
            d = generar_escenario_pais(pais)
            st.download_button("📥 Bajar Excel Simulado", d, f"Simulacion_{pais}.xlsx", mime="application/vnd.ms-excel", use_container_width=True)

    with st.expander("🗺️ API Google Maps"):
        api = st.text_input("API Key (Opcional)", type="password")
        if api: st.session_state['api_key'] = api
        use_g = st.checkbox("Activar Tráfico Real", value=False, disabled=not bool(api))
        if use_g: st.caption("🟢 Conectado a Routes API")

# --- MAIN DASHBOARD ---
st.title("T1 OptiDock Solver (Dual-Dock Edition)")
st.markdown("Motor algorítmico **V24.3** para planificación de **Primera Milla** y **Patios**. Asigna carga y descarga simultáneamente.")

# AREA DE CARGA (Clean Card Style)
uploaded_file = st.file_uploader("", type=['xlsx'], help="Carga aquí tu archivo de pedidos y configuración")

if uploaded_file:
    dp, dc = smart_load(uploaded_file)
    if not dp.empty:
        col_act, col_info = st.columns([1, 2])
        with col_act:
            if st.button("✨ Ejecutar Algoritmo", use_container_width=True):
                with st.spinner("Optimizando rutas y muelles..."):
                    res_dicts = run_optimization_logic(dp, dc, use_google=use_g, google_key=st.session_state['api_key'])
                    res = pd.DataFrame(res_dicts)
                st.session_state['results_df'] = res
        with col_info:
            st.info(f"📁 **Datos Cargados:** {len(dp)} Pedidos | {len(dc)} Muelles Configurados")

# DASHBOARD DE RESULTADOS
if st.session_state['results_df'] is not None:
    df = st.session_state['results_df']
    st.divider()
    
    st.subheader("Resultados de la Asignación")
    
    # 1. KPIs SUPERIORES
    kpi1, kpi2, kpi3 = st.columns(3)
    total_pedidos = len(df)
    asignados = len(df[df['Asignado'] == 'SI'])
    
    kpi1.metric("Total Pedidos", f"{total_pedidos}")
    kpi2.metric("Asignados", f"{asignados}", f"{int(asignados/total_pedidos*100)}%")
    kpi3.metric("No Asignados", f"{total_pedidos - asignados}")
    
    # 2. PESTAÑAS DETALLADAS
    st.markdown("---")
    t1, t2 = st.tabs(["📋 Tabla Principal", "📥 Descargas"])
    
    with t1:
        st.dataframe(df, use_container_width=True)

    with t2:
        st.subheader("Exportar Datos")
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as w: df.to_excel(w, index=False)
        st.download_button("💾 Descargar Resultados (.xlsx)", out.getvalue(), "Resultados_OptiDock.xlsx", use_container_width=True)

