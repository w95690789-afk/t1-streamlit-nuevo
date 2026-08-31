#!/usr/bin/env python3
import os
import sys

# Wrapper local para el gestor de bitácoras del proyecto: t1-streamlit-nuevo
GLOBAL_MANAGER = "/home/wilsonpintogaona/Documentos/Proyectos/Will Proy Antigravity/bitacora_manager.py"
PROJECT_NAME = "t1-streamlit-nuevo"

if not os.path.exists(GLOBAL_MANAGER):
    print(f"Error: No se encontró el gestor global de bitácoras en {GLOBAL_MANAGER}")
    sys.exit(1)

os.execv(sys.executable, [sys.executable, GLOBAL_MANAGER, PROJECT_NAME] + sys.argv[1:])
