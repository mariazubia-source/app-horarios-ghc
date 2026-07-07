import streamlit as st
import pandas as pd
from lxml import etree
import json
import firebase_admin
from firebase_admin import credentials, firestore
import zlib
import base64

st.set_page_config(page_title="Editor GHC Cloud", layout="wide")
st.title("☁️ Gestor Visual de Horarios - Nube Multiusuario")

# --- CONEXIÓN A FIREBASE ---
@st.cache_resource
def iniciar_conexion_bd():
    if not firebase_admin._apps:
        llave_secreta = json.loads(st.secrets["FIREBASE_JSON"])
        credenciales = credentials.Certificate(llave_secreta)
        firebase_admin.initialize_app(credenciales)
    return firestore.client()

db = iniciar_conexion_bd()

def guardar_tabla_en_nube(nombre_tabla, dataframe):
    db.collection('ghc_tablas').document(nombre_tabla).set({'datos': dataframe.to_dict('records')})

def cargar_datos_de_nube():
    doc_xml = db.collection('ghc_sistema').document('plantilla_base').get()
    if not doc_xml.exists: return None, None
    
    datos_doc = doc_xml.to_dict()
    xml_comprimido = datos_doc.get('xml_comprimido')
    
    try:
        xml_bytes = zlib.decompress(base64.b64decode(xml_comprimido))
        tree = etree.ElementTree(etree.fromstring(xml_bytes))
    except Exception as e:
        st.error(f"Error al descomprimir la base de datos: {e}")
        return None, None
    
    coleccion = db.collection('ghc_tablas').stream()
    dfs = {}
    for doc in coleccion:
        dfs[doc.id] = pd.DataFrame(doc.to_dict().get('datos', [])).fillna("")
    return tree, dfs

# --- MEMORIA DE LA SESIÓN ---
if "bd_cargada" not in st.session_state:
    st.session_state.xml_tree, st.session_state.data_frames = cargar_datos_de_nube()
    st.session_state.bd_cargada = True

# --- PANTALLA DE INICIO ---
if st.session_state.xml_tree is None:
    st.info("☁️ La base de datos central está vacía. Sube tu archivo XML de Peñalara por primera vez.")
    uploaded_file = st.file_uploader("📂 Sube tu archivo 'planificador.xml'", type=["xml"])
    
    if uploaded_file is not None:
        parser = etree.XMLParser(encoding='iso-8859-1', strip_cdata=False)
        tree = etree.parse(uploaded_file, parser)
        root = tree.getroot()
        
        dfs = {}
        for container in root:
            if len(container) > 0:
                tag_hijo = container[0].tag
                registros = []
                for i, item in enumerate(container.findall(tag_hijo)):
                    fila = {}
                    identificador = item.get('id') or item.findtext('nombre') or item.get('nombre') or item.get('abreviatura') or f"Elemento_{i}"
                    fila['ID_SISTEMA'] = identificador
                    for k, v in item.attrib.items(): fila[f"@{k}"] = v
                    for child in item:
                        if child.tag in ['listaDeAulas', 'otrasAulas']: fila[child.tag] = ", ".join([c.text for c in child.findall('aula') if c.text])
                        elif child.tag == 'otrosProfesores': fila[child.tag] = ", ".join([c.text for c in child.findall('profesor') if c.text])
                        elif child.tag == 'otrosGrupos': fila[child.tag] = ", ".join([c.text for c in child.findall('grupo') if c.text])
                        elif len(child) == 0: fila[child.tag] = child.text.strip() if child.text else ""
                        else: fila[child.tag] = (child.text or '') + ''.join([etree.tostring(c, encoding='unicode') for c in child])
                    registros.append(fila)
                if registros:
                    dfs[container.tag.capitalize()] = pd.DataFrame(registros).fillna("")
        
        xml_bytes = etree.tostring(root, encoding='ISO-8859-1')
        xml_comprimido = base64.b64encode(zlib.compress(xml_bytes)).decode('utf-8')
        
        db.collection('ghc_sistema').document('plantilla_base').set({'xml_comprimido': xml_comprimido})
        for nombre, df in dfs.items(): guardar_tabla_en_nube(nombre, df)
        
        st.session_state.xml_tree = tree
        st.session_state.data_frames = dfs
        st.rerun()

# --- PANTALLA PRINCIPAL DE EDICIÓN ---
if st.session_state.xml_tree is not None:
    st.success("✅ Base de datos conectada. Los cambios se guardan automáticamente.")
    
    if st.button("🚨 Resetear Base de Datos", type="secondary"):
        db.collection('ghc_sistema').document('plantilla_base').delete()
        st.session_state.xml_tree = None
        st.rerun()
        
    tab_names = list(st.session_state.data_frames.keys())
    tabs = st.tabs(tab_names)
    
    for i, tab in enumerate(tabs):
        with tab:
            df = st.session_state.data_frames[tab_names[i]]
            if df.empty: continue
            
            # --- MEJORA UX 1: Título Semántico ---
            st.markdown(f"### 📋 Gestor de {tab_names[i]}")
            st.caption("Edita directamente en las celdas. Los cambios se sincronizan en la nube al pulsar Enter o salir de la celda.")
            
            # --- MEJORA UX 2: Tabla a pantalla completa ---
            df_editado = st.data_editor(df, use_container_width=True, hide_index=True, key=f"editor_{i}")
            
            if not df_editado.equals(df):
                st.session_state.data_frames[tab_names[i]] = df_editado
                guardar_tabla_en_nube(tab_names[i], df_editado)
                st.toast('☁️ ¡Cambio guardado en la nube!')
            
            st.write("") # Espacio en blanco para dar aire visual
            
            # --- MEJORA UX 3: Inspector en Acordeón (Desplegable) ---
            with st.expander(f"🛠️ Editar configuraciones avanzadas (subcampos) de {tab_names[i]}", expanded=False):
                st.markdown("Selecciona un elemento para desglosar su información interna.")
                opciones = df['ID_SISTEMA'].tolist()
                seleccion = st.selectbox("Elemento a inspeccionar:", ["-- Ninguna --"] + opciones, key=f"sel_{i}")
                
                if seleccion != "-- Ninguna --":
                    idx = df[df['ID_SISTEMA'] == seleccion].index[0]
                    campos_anidados_encontrados = False
                    
                    with st.form(key=f"form_{i}_{seleccion}"):
                        nuevos_valores_xml = {}
                        for col_name in df.columns:
                            valor_actual = str(df.at[idx, col_name])
                            if "<" in valor_actual and ">" in valor_actual:
                                campos_anidados_encontrados = True
                                st.markdown(f"**📂 {col_name.capitalize()}**")
                                try:
                                    sub_tree = etree.fromstring(f"<root>{valor_actual}</root>")
                                    dict_subcampos = {}
                                    
                                    # Usamos columnas dentro del formulario para que quede más compacto
                                    cols_form = st.columns(3)
                                    col_idx = 0
                                    
                                    for child in sub_tree:
                                        val = child.text if child.text else ""
                                        with cols_form[col_idx % 3]:
                                            dict_subcampos[child.tag] = st.text_input(f"↳ {child.tag}", value=val, key=f"inp_{i}_{col_name}_{child.tag}")
                                        col_idx += 1
                                        
                                    nuevos_valores_xml[col_name] = dict_subcampos
                                except:
                                    nuevos_valores_xml[col_name] = st.text_area(f"🔧 {col_name} (Avanzado)", value=valor_actual)
                        
                        if not campos_anidados_encontrados:
                            st.info("Este elemento no tiene configuraciones complejas ocultas. Puedes editarlo cómodamente en la tabla superior.")
                            st.form_submit_button("Cerrar", disabled=True)
                        else:
                            st.write("")
                            if st.form_submit_button("💾 Aplicar y Guardar en la Nube", type="primary"):
                                for col_name, subcampos in nuevos_valores_xml.items():
                                    if isinstance(subcampos, dict):
                                        xml_str = "".join([f"<{tag}>{val}</{tag}>" for tag, val in subcampos.items()])
                                        st.session_state.data_frames[tab_names[i]].at[idx, col_name] = xml_str
                                    else:
                                        st.session_state.data_frames[tab_names[i]].at[idx, col_name] = subcampos
                                
                                guardar_tabla_en_nube(tab_names[i], st.session_state.data_frames[tab_names[i]])
                                st.rerun()

    st.divider()
    
    if st.button("📦 DESCARGAR XML PARA PEÑALARA", type="primary"):
        root = st.session_state.xml_tree.getroot()
        for tab_name, df_editado in st.session_state.data_frames.items():
            nombre_pestana = tab_name.lower()
            container = root.find(nombre_pestana)
            if container is None: continue
            tag_hijo = container[0].tag
            
            for fila in df_editado.to_dict('records'):
                id_sistema = str(fila.get('ID_SISTEMA', ''))
                if not id_sistema or id_sistema.startswith("Elemento_"): continue
                
                nodo = container.find(f"{tag_hijo}[@id='{id_sistema}']") or container.find(f"{tag_hijo}[nombre='{id_sistema}']") or container.find(f"{tag_hijo}[@nombre='{id_sistema}']") or container.find(f"{tag_hijo}[@abreviatura='{id_sistema}']")
                    
                if nodo is not None:
                    for col, valor in fila.items():
                        if col == 'ID_SISTEMA': continue
                        valor_str = str(valor).strip()
                        if col.startswith('@'): nodo.set(col[1:], valor_str)
                        elif col in ['listaDeAulas', 'otrasAulas']:
                            lista_nodo = nodo.find(col)
                            if lista_nodo is None and valor_str: lista_nodo = etree.SubElement(nodo, col)
                            if lista_nodo is not None:
                                for c in list(lista_nodo): lista_nodo.remove(c)
                                for a in valor_str.split(','):
                                    if a.strip(): etree.SubElement(lista_nodo, 'aula').text = a.strip()
                        elif col in ['otrosProfesores', 'otrosGrupos']:
                            tag_interno = 'profesor' if col == 'otrosProfesores' else 'grupo'
                            lista_nodo = nodo.find(col)
                            if lista_nodo is None and valor_str: lista_nodo = etree.SubElement(nodo, col)
                            if lista_nodo is not None:
                                for c in list(lista_nodo): lista_nodo.remove(c)
                                for item_val in valor_str.split(','):
                                    if item_val.strip(): etree.SubElement(lista_nodo, tag_interno).text = item_val.strip()
                        else:
                            hijo = nodo.find(col)
                            if '<' in valor_str and '>' in valor_str:
                                try:
                                    nuevo_hijo = etree.fromstring(f"<{col}>{valor_str}</{col}>")
                                    if hijo is not None: nodo.replace(hijo, nuevo_hijo)
                                    else: nodo.append(nuevo_hijo)
                                    continue
                                except: pass
                            if hijo is not None:
                                for c in list(hijo): hijo.remove(c)
                                hijo.text = valor_str
                            elif valor_str: etree.SubElement(nodo, col).text = valor_str

        xml_str = etree.tostring(root, encoding='ISO-8859-1', xml_declaration=True, pretty_print=True)
        st.download_button(label="📥 HAZ CLIC AQUÍ PARA DESCARGAR EL ARCHIVO FINAL", data=xml_str, file_name="PLANIFICADOR_NUBE.xml", mime="application/xml")
