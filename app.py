import streamlit as st
import pandas as pd
from lxml import etree
import json
import firebase_admin
from firebase_admin import credentials, firestore
import zlib
import base64

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Editor GHC Cloud", layout="wide", initial_sidebar_state="expanded")
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

# --- MOTOR RECURSIVO PARA ANIDAMIENTO PROFUNDO ---
def render_recursive_xml_form(node, path_key, depth=0):
    """Dibuja de forma infinita inputs para XML jerárquicos (ej. restricciones y plantillas)"""
    indent = "&nbsp;" * (depth * 6)
    
    # 1. Dibujar Atributos (Muy comunes en Peñalara para definir plantillas)
    if node.attrib:
        for k, v in node.attrib.items():
            c1, c2 = st.columns([1, 3])
            with c1: st.markdown(f"{indent}⚙️ `@<b>{k}</b>`", unsafe_allow_html=True)
            with c2: node.attrib[k] = st.text_input(f"{k}", value=v, key=f"attr_{path_key}_{k}", label_visibility="collapsed")
            
    # 2. Dibujar Hijos o Valor de texto
    if len(node) > 0:
        for i, child in enumerate(node):
            st.markdown(f"{indent}📂 <b>{child.tag}</b>", unsafe_allow_html=True)
            # Llamada recursiva para procesar a los "nietos", "bisnietos", etc.
            render_recursive_xml_form(child, f"{path_key}_{i}_{child.tag}", depth + 1)
    else:
        # Si es un elemento final (sin hijos), mostramos su caja de texto
        c1, c2 = st.columns([1, 3])
        with c1: st.markdown(f"{indent}↳ {node.tag}", unsafe_allow_html=True)
        with c2: node.text = st.text_input(f"{node.tag}", value=node.text or "", key=f"val_{path_key}", label_visibility="collapsed")

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
                    
                    # Extraer atributos principales
                    for k, v in item.attrib.items(): fila[f"@{k}"] = v
                    
                    for child in item:
                        # Extraer listas simples
                        if child.tag in ['listaDeAulas', 'otrasAulas']: 
                            fila[child.tag] = ", ".join([c.text for c in child.findall('aula') if c.text])
                        elif child.tag == 'otrosProfesores': 
                            fila[child.tag] = ", ".join([c.text for c in child.findall('profesor') if c.text])
                        elif child.tag == 'otrosGrupos': 
                            fila[child.tag] = ", ".join([c.text for c in child.findall('grupo') if c.text])
                        # Extraer nodos vacíos
                        elif len(child) == 0 and not child.attrib: 
                            fila[child.tag] = child.text.strip() if child.text else ""
                        else: 
                            # Nodo complejo (Restricciones, Plantillas). Se guarda como XML crudo.
                            fila[child.tag] = etree.tostring(child, encoding='unicode')
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
    # --- MENÚ LATERAL MEJORADO (NAVEGACIÓN) ---
    st.sidebar.markdown("### 🗺️ Menú de Navegación")
    tab_names = list(st.session_state.data_frames.keys())
    
    # Hemos sustituido los st.tabs por un selector en la barra lateral
    selected_tab = st.sidebar.radio("Selecciona qué sección editar:", tab_names)
    
    st.sidebar.divider()
    if st.sidebar.button("🚨 Reiniciar Base de Datos", type="secondary"):
        db.collection('ghc_sistema').document('plantilla_base').delete()
        st.session_state.xml_tree = None
        st.rerun()
        
    st.sidebar.divider()
    # Mover el botón de descarga al menú lateral para no ensuciar la pantalla principal
    st.sidebar.markdown("### 💾 Exportación")
    btn_descarga = st.sidebar.empty() # Espacio reservado para el botón al final del script

    # --- ÁREA PRINCIPAL DE TRABAJO ---
    df = st.session_state.data_frames[selected_tab]
    
    st.markdown(f"### 📋 Gestor de {selected_tab}")
    st.caption("Edita directamente en las celdas. Los cambios se sincronizan en la nube automáticamente.")
    
    df_editado = st.data_editor(df, use_container_width=True, hide_index=True, key=f"editor_{selected_tab}")
    
    if not df_editado.equals(df):
        st.session_state.data_frames[selected_tab] = df_editado
        guardar_tabla_en_nube(selected_tab, df_editado)
        st.toast('☁️ ¡Cambio guardado en la nube!')
    
    st.write("")
    
    # --- INSPECTOR DE RESTRICCIONES Y ANIDAMIENTO PROFUNDO ---
    with st.expander(f"🛠️ Editar configuraciones avanzadas y restricciones de {selected_tab}", expanded=False):
        opciones = df['ID_SISTEMA'].tolist()
        seleccion = st.selectbox("Elemento a inspeccionar:", ["-- Ninguna --"] + opciones, key="sel_avanzado")
        
        if seleccion != "-- Ninguna --":
            idx = df[df['ID_SISTEMA'] == seleccion].index[0]
            # Buscamos campos que sean strings XML (donde viven las restricciones)
            campos_complejos = [col for col in df.columns if isinstance(df.at[idx, col], str) and str(df.at[idx, col]).strip().startswith('<')]
            
            if not campos_complejos:
                st.info("Este elemento no tiene estructuras de restricciones complejas u ocultas. Todo se puede editar desde la tabla superior.")
            else:
                st.markdown("### Estructuras Anidadas Encontradas")
                st.caption("A continuación se muestra el árbol de restricciones completo. Modifica valores o atributos (⚙️).")
                
                with st.form(key=f"form_anidado_{seleccion}"):
                    nuevos_valores_xml = {}
                    
                    for col_name in campos_complejos:
                        valor_actual = str(df.at[idx, col_name])
                        st.markdown(f"#### 📦 {col_name.capitalize()}")
                        try:
                            # Convertimos el texto nuevamente a un árbol XML
                            sub_tree = etree.fromstring(valor_actual)
                            # Renderizamos la estructura recursiva infinita
                            render_recursive_xml_form(sub_tree, path_key=f"{seleccion}_{col_name}")
                            nuevos_valores_xml[col_name] = sub_tree
                        except Exception as e:
                            st.error("Formato XML irreconocible para editor visual.")
                            nuevos_valores_xml[col_name] = st.text_area(f"Modo Texto", value=valor_actual)
                            
                    st.write("")
                    if st.form_submit_button("💾 Aplicar Cambios Profundos a la Nube", type="primary"):
                        for col_name, obj in nuevos_valores_xml.items():
                            if isinstance(obj, str):
                                st.session_state.data_frames[selected_tab].at[idx, col_name] = obj
                            else:
                                st.session_state.data_frames[selected_tab].at[idx, col_name] = etree.tostring(obj, encoding='unicode')
                        
                        guardar_tabla_en_nube(selected_tab, st.session_state.data_frames[selected_tab])
                        st.rerun()

    # --- GENERADOR DE DESCARGA PEÑALARA ---
    root = st.session_state.xml_tree.getroot()
    for tab_name, dataframe_editado in st.session_state.data_frames.items():
        nombre_pestana = tab_name.lower()
        container = root.find(nombre_pestana)
        if container is None: continue
        tag_hijo = container[0].tag
        
        for fila in dataframe_editado.to_dict('records'):
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
                        # Reinsertar nodos complejos (restricciones) editados
                        if isinstance(valor_str, str) and valor_str.startswith('<') and valor_str.endswith('>'):
                            try:
                                nuevo_hijo = etree.fromstring(valor_str)
                                if hijo is not None: nodo.replace(hijo, nuevo_hijo)
                                else: nodo.append(nuevo_hijo)
                                continue
                            except: pass
                            
                        if hijo is not None:
                            for c in list(hijo): hijo.remove(c)
                            hijo.text = valor_str
                        elif valor_str: etree.SubElement(nodo, col).text = valor_str

    xml_str = etree.tostring(root, encoding='ISO-8859-1', xml_declaration=True, pretty_print=True)
    # Colocamos el botón en el menú lateral usando el hueco reservado
    with btn_descarga:
        st.download_button(label="📥 DESCARGAR PLANIFICADOR.XML", data=xml_str, file_name="PLANIFICADOR_NUBE.xml", mime="application/xml", use_container_width=True)
