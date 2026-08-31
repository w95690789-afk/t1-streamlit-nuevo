import math
import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pandas as pd
from ortools.sat.python import cp_model

# ==========================================
# UTILIDADES DE TIEMPO Y DISTANCIA
# ==========================================

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula la distancia en km entre dos coordenadas usando la fórmula Haversine."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def calculate_eta(lat1: float, lon1: float, lat2: float, lon2: float,
                  speed_kmh: float = 40.0, tortuosity: float = 1.3,
                  use_google: bool = False, google_key: str = "") -> int:
    """
    Calcula el tiempo de tránsito en minutos.
    Haversine interno por defecto (30 km/h, factor tortuosidad 1.3).
    Google Maps si se provee key válida.
    """
    if use_google and google_key:
        try:
            endpoint = "https://routes.googleapis.com/directions/v2:computeRoutes"
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": google_key,
                "X-Goog-FieldMask": "routes.duration"
            }
            body = {
                "origin":      {"location": {"latLng": {"latitude": lat1, "longitude": lon1}}},
                "destination": {"location": {"latLng": {"latitude": lat2, "longitude": lon2}}},
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
                "units": "METRIC"
            }
            response = requests.post(endpoint, json=body, headers=headers, timeout=5)
            data = response.json()
            if "routes" in data and data["routes"]:
                duration_sec = int(data["routes"][0]["duration"].replace("s", ""))
                return math.ceil(duration_sec / 60.0)
        except Exception as e:
            print(f"⚠️  Google Maps API falló ({e}), usando Haversine fallback.")

    # Haversine interno — velocidad estándar logística 40 km/h
    dist_km = haversine_distance(lat1, lon1, lat2, lon2) * tortuosity
    time_hours = dist_km / max(speed_kmh, 1.0)
    return max(1, math.ceil(time_hours * 60.0))   # mínimo 1 minuto

def format_minutes_to_time(minutes: int, ref_date_str: str) -> str:
    """Convierte minutos desde medianoche a string YYYY-MM-DD HH:MM."""
    try:
        ref_dt = datetime.strptime(ref_date_str, "%Y-%m-%d")
        target_dt = ref_dt + timedelta(minutes=int(minutes))
        return target_dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return f"T+{minutes}min"

def _hhmm_to_min(val) -> int:
    """Convierte HH:MM string o número de minutos a int minutos desde medianoche."""
    if val is None:
        return 0
    s = str(val).strip()
    if ':' in s:
        parts = s.split(':')
        return int(parts[0]) * 60 + int(parts[1])
    try:
        f = float(s)
        # Si es un float pequeño (<25) se interpreta como horas
        return int(f * 60) if f < 25 else int(f)
    except ValueError:
        return 0

def _ventanas_from_config(m_config: dict) -> list:
    """
    Extrae ventanas (inicio_min, fin_min) desde la config de un muelle.
    Prioridad de columnas:
      1. Inicio / Fin  (nombres canónicos del usuario)
      2. Ventanas_Absolutas (array multi-ventana)
      3. Ventana_Inicio / Ventana_Fin (nombres legacy)
    """
    # ── 1. Columnas primarias: Inicio / Fin ───────────────────────────────────
    raw_inicio = m_config.get('Inicio')
    raw_fin    = m_config.get('Fin')
    if raw_inicio is not None and raw_fin is not None:
        vs = _hhmm_to_min(raw_inicio)
        ve = _hhmm_to_min(raw_fin)
        if ve > vs:
            return [(vs, ve)]

    # ── 2. Array multi-ventana Ventanas_Absolutas ─────────────────────────────
    v_raw = m_config.get('Ventanas_Absolutas')
    if v_raw and isinstance(v_raw, list) and len(v_raw) > 0:
        ventanas = []
        for w in v_raw:
            if isinstance(w, (list, tuple)) and len(w) >= 2:
                ws = _hhmm_to_min(w[0])
                we = _hhmm_to_min(w[1])
                ventanas.append((ws, we))
            elif isinstance(w, dict):
                ws = _hhmm_to_min(w.get('inicio', w.get('start', '0')))
                we = _hhmm_to_min(w.get('fin',    w.get('end',  '1440')))
                ventanas.append((ws, we))
        if ventanas:
            return ventanas

    # ── 3. Fallback legacy: Ventana_Inicio / Ventana_Fin ─────────────────────
    vs = _hhmm_to_min(m_config.get('Ventana_Inicio', '0'))
    ve = _hhmm_to_min(m_config.get('Ventana_Fin',   '1440'))
    return [(vs, ve)]

def _dock_fecha_is_valid(m_config: dict, fecha_planificacion: str) -> bool:
    """
    Devuelve True si el muelle es válido para la fecha de planificación.
    Si el muelle no tiene campo 'Fecha', aplica a todas las fechas (disponible siempre).
    Si tiene 'Fecha', solo aplica cuando coincide exactamente con fecha_planificacion.
    Acepta formatos: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY.
    """
    fecha_m = str(m_config.get('Fecha', '') or '').strip()
    if not fecha_m:
        return True  # Sin restricción de fecha → siempre disponible
    # Normalizar a YYYY-MM-DD
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(fecha_m, fmt).strftime('%Y-%m-%d') == fecha_planificacion
        except ValueError:
            continue
    return fecha_m == fecha_planificacion  # comparación literal como último recurso



# ==========================================
# MOTOR DE OPTIMIZACIÓN CP-SAT V24.3 — DUAL-DOCK
# ==========================================

def run_optimization_logic(df_pedidos: pd.DataFrame,
                           df_config: pd.DataFrame,
                           use_google: bool = False,
                           google_key: str = "",
                           speed_params: float = 30.0,        # 30 km/h estándar logística
                           tortuosity_params: float = 1.3,
                           fecha_inicio_str: str = "2026-01-01"):
    """
    Motor CP-SAT de ocupación de muelles — Versión Dual-Dock.

    Novedades V24.3:
    ─────────────────────────────────────────────────────────────────────────────
    • `Min_Operacion` del muelle override la duración del pedido (task duration).
    • Haversine interno a 30 km/h para el tránsito origen→destino.
    • Lógica DUAL: el CP-SAT resuelve simultáneamente muelle de CARGA (origen)
                   y muelle de DESCARGA (destino).  Cada uno tiene sus propias
                   ventanas de disponibilidad y restricciones de no-solapamiento.
    • Pedidos no asignables generan log descriptivo en lugar de lanzar excepción.
    ─────────────────────────────────────────────────────────────────────────────
    """

    print(f"🚀 CP-SAT V24.3 DUAL-DOCK | Pedidos: {len(df_pedidos)} | Muelles: {len(df_config)}")

    pedidos_input = df_pedidos.to_dict('records')
    config_input  = df_config.to_dict('records')

    # ── Mapa canónico: índice → configuración del muelle ──────────────────────────
    # Único punto de verdad para extraer Muelle_ID y Nombre_Nodo en el resultado.
    # Se construye ANTES de entrar al modelo para no depender de referencias de tupla.
    mapa_muelles: Dict[int, dict] = {idx: cfg for idx, cfg in enumerate(config_input)}
    print(f"🗂️  mapa_muelles construido: {list(mapa_muelles.keys())} "
          f"| IDs: {[cfg.get('Muelle_ID', cfg.get('muelle_id','??')) for cfg in config_input]}")

    model  = cp_model.CpModel()
    HORIZON       = 1440          # minutos en un día
    COORD_EPSILON = 0.005         # tolerancia de búsqueda de nodo ~500m

    # ── Índice de nodos (por coordenada) ──────────────────────────────────────
    nodos_validos: Dict[tuple, list] = {}
    for c in config_input:
        lat = float(c.get('Latitud', c.get('Origen_Lat', 0)))
        lon = float(c.get('Longitud', c.get('Origen_Lon', 0)))
        key = (round(lat, 4), round(lon, 4))
        nodos_validos.setdefault(key, []).append(c)

    def find_candidate_muelles(lat: float, lon: float) -> list:
        """
        Devuelve muelles dentro de COORD_EPSILON cuya Fecha sea compatible
        con la fecha de planificación (o sin restricción de fecha).
        """
        key = (round(lat, 4), round(lon, 4))
        for n_key, m_list in nodos_validos.items():
            if abs(n_key[0] - key[0]) < COORD_EPSILON and abs(n_key[1] - key[1]) < COORD_EPSILON:
                return [m for m in m_list if _dock_fecha_is_valid(m, fecha_inicio_str)]
        return []

    # ── Estructuras del modelo ────────────────────────────────────────────────
    # intervals_per_dock[m_idx] = lista de intervalos CP-SAT para ese muelle
    intervals_per_dock: Dict[int, list] = {i: [] for i in range(len(config_input))}
    assigned_info:   list = []
    unassigned_info: list = []

    # ── Loop por pedido ───────────────────────────────────────────────────────
    for p_idx, p in enumerate(pedidos_input):
        p_id  = p.get('ID', p.get('id', f"P{p_idx}"))
        p_lat = float(p.get('Origen_Lat', 0) or 0)
        p_lon = float(p.get('Origen_Lon', 0) or 0)
        d_lat = float(p.get('Destino_Lat', p_lat) or p_lat)
        d_lon = float(p.get('Destino_Lon', p_lon) or p_lon)

        # Skills requeridos — normalización robusta (lower, strip, guión→guión_bajo)
        def _norm_skill(s: str) -> str:
            return s.strip().lower().replace('-', '_').replace(' ', '_')
        p_skills = [_norm_skill(s) for s in str(p.get('Skills_Requeridos', '') or '').split(',') if s.strip()]

        # Muelles en el nodo de ORIGEN (carga)
        carga_candidates   = find_candidate_muelles(p_lat, p_lon)
        # Muelles en el nodo de DESTINO (descarga)
        descarga_candidates = find_candidate_muelles(d_lat, d_lon)

        motivos = []

        if not carga_candidates:
            motivos.append(f"Sin muelle de carga en ({round(p_lat,4)},{round(p_lon,4)})")
        if not descarga_candidates:
            # Si origen==destino es válido usar el mismo nodo para descarga
            if abs(p_lat - d_lat) < COORD_EPSILON and abs(p_lon - d_lon) < COORD_EPSILON:
                descarga_candidates = carga_candidates
            else:
                motivos.append(f"Sin muelle de descarga en ({round(d_lat,4)},{round(d_lon,4)})")

        if motivos:
            unassigned_info.append({"ID_Pedido": p_id, "Asignado": "NO",
                                    "Motivo": "; ".join(motivos)})
            continue

        # ── Tiempo de tránsito origen → destino (Haversine 30 km/h) ──────────
        eta_viaje = calculate_eta(p_lat, p_lon, d_lat, d_lon,
                                  speed_kmh=speed_params,
                                  tortuosity=tortuosity_params,
                                  use_google=use_google,
                                  google_key=google_key)

        # ── Variables de tiempo del ciclo completo ────────────────────────────
        start_load   = model.NewIntVar(0, HORIZON, f'sl_{p_idx}')
        end_load     = model.NewIntVar(0, HORIZON, f'el_{p_idx}')
        arrival_dest = model.NewIntVar(0, HORIZON, f'ad_{p_idx}')
        start_unload = model.NewIntVar(0, HORIZON, f'su_{p_idx}')
        end_unload   = model.NewIntVar(0, HORIZON, f'eu_{p_idx}')

        # Encadenamiento temporal:  CARGA → TRÁNSITO → DESCARGA
        model.Add(arrival_dest == end_load + eta_viaje)
        model.Add(start_unload >= arrival_dest)

        # ── MUELLES DE CARGA (ORIGEN) ─────────────────────────────────────────
        carga_pres_vars   = []
        carga_dur_var     = model.NewIntVar(0, HORIZON, f'carga_dur_{p_idx}')

        has_valid_carga_muelle = False

        for m_idx, m_cfg in enumerate(config_input):
            if m_cfg not in carga_candidates:
                continue
            m_skill = _norm_skill(str(m_cfg.get('Skill_Soportado', '') or ''))
            if p_skills and m_skill not in p_skills and m_skill not in ('general', ''):
                continue

            # Min_Operacion del muelle overrides duración del pedido
            min_op = m_cfg.get('Min_Operacion')
            if min_op is not None and str(min_op).strip() not in ('', 'None'):
                t_carga = max(1, int(float(min_op)))
            else:
                raw = p.get('Tiempo_Carga_min', p.get('Tiempo_Carga_h'))
                if raw is None:
                    t_carga = 60
                elif p.get('Tiempo_Carga_h') is not None and p.get('Tiempo_Carga_min') is None:
                    t_carga = max(1, int(float(raw) * 60))
                else:
                    t_carga = max(1, int(float(raw)))

            p_var = model.NewBoolVar(f'pc_{p_idx}_{m_idx}')
            carga_pres_vars.append((m_idx, p_var, m_cfg, t_carga))
            has_valid_carga_muelle = True

            # Intervalo condicional en el muelle de carga
            # end_load = start_load + t_carga  → codificado via intervalo fijo
            interval_c = model.NewOptionalIntervalVar(
                start_load, t_carga, end_load, p_var, f'ivc_{p_idx}_{m_idx}'
            )
            intervals_per_dock[m_idx].append(interval_c)

            # Ventanas de disponibilidad del muelle de carga
            ventanas = _ventanas_from_config(m_cfg)
            print(f"  [CARGA] Pedido={p_id} Muelle={m_cfg.get('Muelle_ID')} "
                  f"skill={m_cfg.get('Skill_Soportado')} dur={t_carga}min "
                  f"ventanas={ventanas} fecha={m_cfg.get('Fecha','-')}")
            _add_window_constraints(model, start_load, end_load, p_var, ventanas,
                                    f'cw_{p_idx}_{m_idx}')

        if not has_valid_carga_muelle:
            motivo_skills = "Ningún muelle de carga compatible con skills requeridos" if p_skills else "Sin muelles de carga válidos"
            unassigned_info.append({"ID_Pedido": p_id, "Asignado": "NO", "Motivo": motivo_skills})
            continue

        # Al menos 1 y máximo 1 muelle de carga
        model.Add(sum(pv for _, pv, _, _ in carga_pres_vars) == 1)

        # Duración real de la carga = Min_Operacion del muelle asignado
        # Codificamos carga_dur_var como variable auxiliar para el objetivo
        for m_idx, p_var, m_cfg, t_c in carga_pres_vars:
            model.Add(carga_dur_var == t_c).OnlyEnforceIf(p_var)

        # ── MUELLES DE DESCARGA (DESTINO) ─────────────────────────────────────
        descarga_pres_vars = []
        has_valid_desc_muelle = False

        for m_idx, m_cfg in enumerate(config_input):
            if m_cfg not in descarga_candidates:
                continue
            m_skill = _norm_skill(str(m_cfg.get('Skill_Soportado', '') or ''))
            if p_skills and m_skill not in p_skills and m_skill not in ('general', ''):
                continue

            # Min_Operacion del muelle de descarga
            min_op_d = m_cfg.get('Min_Operacion')
            if min_op_d is not None and str(min_op_d).strip() not in ('', 'None'):
                t_desc = max(1, int(float(min_op_d)))
            else:
                raw_d = p.get('Tiempo_Descarga_min', p.get('Tiempo_Descarga_h'))
                if raw_d is None:
                    t_desc = 60
                elif p.get('Tiempo_Descarga_h') is not None and p.get('Tiempo_Descarga_min') is None:
                    t_desc = max(1, int(float(raw_d) * 60))
                else:
                    t_desc = max(1, int(float(raw_d)))

            end_unload_fixed = model.NewIntVar(0, HORIZON, f'eu_f_{p_idx}_{m_idx}')

            p_var_d = model.NewBoolVar(f'pd_{p_idx}_{m_idx}')
            descarga_pres_vars.append((m_idx, p_var_d, m_cfg, t_desc, end_unload_fixed))
            has_valid_desc_muelle = True

            model.Add(end_unload_fixed == start_unload + t_desc).OnlyEnforceIf(p_var_d)

            interval_d = model.NewOptionalIntervalVar(
                start_unload, t_desc, end_unload_fixed, p_var_d, f'ivd_{p_idx}_{m_idx}'
            )
            intervals_per_dock[m_idx].append(interval_d)

            ventanas_d = _ventanas_from_config(m_cfg)
            print(f"  [DESCARGA] Pedido={p_id} Muelle={m_cfg.get('Muelle_ID')} "
                  f"skill={m_cfg.get('Skill_Soportado')} dur={t_desc}min "
                  f"ventanas={ventanas_d} fecha={m_cfg.get('Fecha','-')}")
            _add_window_constraints(model, start_unload, end_unload_fixed, p_var_d, ventanas_d,
                                    f'dw_{p_idx}_{m_idx}')

        if not has_valid_desc_muelle:
            # Si no hay muelle de descarga distinto, reutilizamos el de carga
            # (pedido de ida sin punto de destino separado)
            # En V24.3 lo marcamos como asignable, la descarga queda como "misma bodega"
            has_valid_desc_muelle = True
            model.Add(start_unload == end_load + eta_viaje)
            model.Add(end_unload == start_unload)  # descarga instantánea
            descarga_pres_vars = []

        if descarga_pres_vars:
            model.Add(sum(pv for _, pv, _, _, _ in descarga_pres_vars) == 1)

        # ── Vinculación end_load con t_carga ──────────────────────────────────
        # end_load = start_load + t_carga del muelle asignado (encadenado via carga_dur_var)
        model.Add(end_load == start_load + carga_dur_var)

        # ── Vinculación end_unload ← end_unload_fixed del muelle de descarga asignado ──────
        # CRÍTICO: sin esto, end_unload queda libre en [0, HORIZON] y no refleja
        # la restricción real. Hora_Fin_Descarga sería incorrecta en el resultado.
        for m_idx, p_var_d, m_cfg, t_desc, eu_fixed in descarga_pres_vars:
            model.Add(end_unload == eu_fixed).OnlyEnforceIf(p_var_d)

        assigned_info.append({
            "p_idx":               p_idx,
            "p_id":                p_id,
            "carga_pres_vars":     carga_pres_vars,      # [(m_idx, bool_var, m_cfg, t_carga)]
            "descarga_pres_vars":  descarga_pres_vars,   # [(m_idx, bool_var, m_cfg, t_desc, eu_fixed)]
            "times": {
                "start_load":   start_load,
                "end_load":     end_load,
                "arrival_dest": arrival_dest,
                "start_unload": start_unload,
                "end_unload":   end_unload,
                "carga_dur":    carga_dur_var,
            }
        })

    # ── 2. Anti-solapamiento por muelle ──────────────────────────────────────
    for m_idx, ivs in intervals_per_dock.items():
        if ivs:
            model.AddNoOverlap(ivs)

    # ── 3. Objetivo: Maximizar minutos operativos asignados ───────────────────
    obj_terms = []
    for info in assigned_info:
        for _, pv, _, t_c in info['carga_pres_vars']:
            obj_terms.append(pv * t_c)
        for _, pv, _, t_d, _ in info['descarga_pres_vars']:
            obj_terms.append(pv * t_d)

    if obj_terms:
        model.Maximize(sum(obj_terms))

    # ── 4. Resolver ───────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    status = solver.Solve(model)
    print(f"📊 CP-SAT status: {solver.StatusName(status)} | tiempo: {solver.WallTime():.2f}s")

    # ── Diagnóstico INFEASIBLE / MODEL_INVALID ────────────────────────────────
    if status in (cp_model.INFEASIBLE, cp_model.MODEL_INVALID):
        print(f"🚨 CP-SAT {solver.StatusName(status)} — Diagnóstico de restricciones por pedido:")
        for info in assigned_info:
            p_raw = pedidos_input[info['p_idx']]
            print(f"  ├── Pedido {info['p_id']}")
            print(f"  │   Skills requeridos : {p_raw.get('Skills_Requeridos', 'N/A')}")
            print(f"  │   Muelles CARGA     : {len(info['carga_pres_vars'])}")
            for m_idx, _, m_cfg, t_c in info['carga_pres_vars']:
                v = _ventanas_from_config(m_cfg)
                print(f"  │     [Muelle {m_cfg.get('Muelle_ID')} idx={m_idx}] "
                      f"skill={m_cfg.get('Skill_Soportado')} dur={t_c}min ventanas={v}")
            print(f"  │   Muelles DESCARGA  : {len(info['descarga_pres_vars'])}")
            for m_idx, _, m_cfg, t_d, _ in info['descarga_pres_vars']:
                v = _ventanas_from_config(m_cfg)
                print(f"  │     [Muelle {m_cfg.get('Muelle_ID')} idx={m_idx}] "
                      f"skill={m_cfg.get('Skill_Soportado')} dur={t_d}min ventanas={v}")
            # Clasificar causa probable
            if not info['carga_pres_vars']:
                print("  │   ❌ CAUSA: Sin muelles de CARGA válidos (Skill o Fecha)")
            elif not info['descarga_pres_vars']:
                print("  │   ❌ CAUSA: Sin muelles de DESCARGA válidos (Skill o Fecha)")
            else:
                total_carga_min = sum(t for _, _, _, t in info['carga_pres_vars'])
                total_desc_min  = sum(t for _, _, _, t, _ in info['descarga_pres_vars'])
                print(f"  │   ❌ CAUSA probable: Ventanas insuficientes para ciclo "
                      f"(carga≈{total_carga_min}min + tránsito + desc≈{total_desc_min}min)")
            print("  │")

    # ── 5. Construir resultados ────────────────────────────────────────────────
    resultados_finales = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for info in assigned_info:
            t = info['times']
            carga_idx   = None
            descarga_idx = None

            # ── Detectar qué muelle de CARGA eligió el solver ────────────────────
            for m_idx, pv, _, _ in info['carga_pres_vars']:
                if solver.Value(pv) == 1:
                    carga_idx = m_idx
                    break

            # ── Detectar qué muelle de DESCARGA eligió el solver ───────────────
            for m_idx, pv, _, _, _ in info['descarga_pres_vars']:
                if solver.Value(pv) == 1:
                    descarga_idx = m_idx
                    break

            # ── Resolver nombres desde mapa canónico ─────────────────────────
            if carga_idx is None:
                # ningún pv = 1 fue encontrado (no debería ocurrir con OPTIMAL)
                resultados_finales.append({
                    "ID_Pedido": info['p_id'], "Asignado": "NO",
                    "Motivo": "CP-SAT OPTIMAL pero solver.Value(pv)==0 para todos los muelles de carga"
                })
                continue

            if carga_idx not in mapa_muelles:
                print(f"❌ ERROR CRÍTICO: carga_idx={carga_idx} no encontrado en mapa_muelles. "
                      f"IDs disponibles: {list(mapa_muelles.keys())}")
                resultados_finales.append({
                    "ID_Pedido": info['p_id'], "Asignado": "NO",
                    "Motivo": f"Error interno: índice de muelle {carga_idx} fuera del mapa"
                })
                continue

            mc = mapa_muelles[carga_idx]
            muelle_id_carga   = mc.get('Muelle_ID') or mc.get('muelle_id') or f"idx-{carga_idx}"
            nombre_nodo_carga = mc.get('Nombre_Nodo') or mc.get('nombre_nodo') or mc.get('Nombre_Bodega') or 'NODO_DESCONOCIDO'

            print(f"  [ASIGNADO CARGA] Pedido={info['p_id']} → "
                  f"idx={carga_idx} Muelle_ID={muelle_id_carga} Nodo={nombre_nodo_carga}")

            if descarga_idx is not None:
                if descarga_idx not in mapa_muelles:
                    print(f"❌ ERROR CRÍTICO: descarga_idx={descarga_idx} no encontrado en mapa_muelles. "
                          f"IDs disponibles: {list(mapa_muelles.keys())}")
                    muelle_id_desc   = 'ERROR'
                    nombre_nodo_desc = 'ERROR'
                else:
                    md = mapa_muelles[descarga_idx]
                    muelle_id_desc   = md.get('Muelle_ID') or md.get('muelle_id') or f"idx-{descarga_idx}"
                    nombre_nodo_desc = md.get('Nombre_Nodo') or md.get('nombre_nodo') or md.get('Nombre_Bodega') or 'NODO_DESCONOCIDO'
                    print(f"  [ASIGNADO DESCARGA] Pedido={info['p_id']} → "
                          f"idx={descarga_idx} Muelle_ID={muelle_id_desc} Nodo={nombre_nodo_desc}")
            else:
                muelle_id_desc   = 'Misma Bodega'
                nombre_nodo_desc = 'Misma Bodega'

            resultados_finales.append({
                "ID_Pedido":           info['p_id'],
                "Asignado":            "SI",
                "Nodo_Carga":          nombre_nodo_carga,
                "Muelle_ID":           muelle_id_carga,
                "Hora_Inicio_Carga":   format_minutes_to_time(solver.Value(t['start_load']),   fecha_inicio_str),
                "Hora_Fin_Carga":      format_minutes_to_time(solver.Value(t['end_load']),     fecha_inicio_str),
                "Hora_Llegada_Destino":format_minutes_to_time(solver.Value(t['arrival_dest']), fecha_inicio_str),
                "Hora_Inicio_Descarga":format_minutes_to_time(solver.Value(t['start_unload']), fecha_inicio_str),
                "Hora_Fin_Descarga":   format_minutes_to_time(solver.Value(t['end_unload']),   fecha_inicio_str),
                "Nodo_Descarga":       nombre_nodo_desc,
                "Muelle_Descarga_ID":  muelle_id_desc,
            })
    else:
        status_name = solver.StatusName(status)
        for info in assigned_info:
            resultados_finales.append({
                "ID_Pedido": info['p_id'], "Asignado": "NO",
                "Motivo": f"CP-SAT infactible ({status_name}): sin espacio en ningún muelle dentro de las ventanas configuradas"
            })

    # Agregar no-asignables pre-modelo
    resultados_finales.extend(unassigned_info)

    total   = len(resultados_finales)
    asign   = sum(1 for r in resultados_finales if r.get('Asignado') == 'SI')
    print(f"✅ Resultado: {asign}/{total} pedidos asignados.")
    return resultados_finales


# ── Helper: restricciones de ventanas CP-SAT ─────────────────────────────────
def _add_window_constraints(model, start_var, end_var, pres_var, ventanas: list, prefix: str):
    """
    Añade restricciones de ventana al modelo CP-SAT.
    Si hay 1 ventana: restricción directa.
    Si hay N ventanas: disjunción (el pedido debe caber en AL MENOS UNA).
    """
    if len(ventanas) == 1:
        model.Add(start_var >= ventanas[0][0]).OnlyEnforceIf(pres_var)
        model.Add(end_var   <= ventanas[0][1]).OnlyEnforceIf(pres_var)
    else:
        in_w_vars = []
        for i, (ws, we) in enumerate(ventanas):
            in_w = model.NewBoolVar(f'{prefix}_w{i}')
            in_w_vars.append(in_w)
            model.Add(start_var >= ws).OnlyEnforceIf([pres_var, in_w])
            model.Add(end_var   <= we).OnlyEnforceIf([pres_var, in_w])
        model.Add(sum(in_w_vars) >= 1).OnlyEnforceIf(pres_var)
        model.Add(sum(in_w_vars) <= 1).OnlyEnforceIf(pres_var)
