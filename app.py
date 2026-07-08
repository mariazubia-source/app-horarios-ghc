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
        st.error(f"Error al descomprimir: {e}")
        return None, None
    
    coleccion = db.collection('ghc_tablas').stream()
    dfs = {}
    for doc in coleccion:
        dfs[doc.id] = pd.DataFrame(doc.to_dict().get('datos', [])).fillna("")
    return tree, dfs

# --- CONVERSORES DE XML A SUB-TABLAS (NIVEL PROFUNDO) ---
def xml_to_df(xml_str):
    tree = etree.fromstring(xml_str)
    parent_tag = tree.tag
    rows = []
    for child in tree:
        row = {'Etiqueta': child.tag, 'Valor_Texto': child.text.strip() if child.text and child.text.strip() else ""}
        for k, v in child.attrib.items():
            row[f"@{k}"] = v
            
        if len(child) > 0:
            # Envolvemos los hijos en una etiqueta sintética para que sea un XML válido en el siguiente nivel
            hijos_str = "".join([etree.tostring(c, encoding='unicode') for c in child])
            row['Sub_Nodos_Ocultos'] = f"<Nodos_{child.tag}>{hijos_str}</Nodos_{child.tag}>"
            
        rows.append(row)
        
    if not rows:
        return parent_tag, pd.DataFrame(columns=['Etiqueta', 'Valor_Texto'])
    return parent_tag, pd.DataFrame(rows).fillna("")

def df_to_xml(parent_tag, df):
    root = etree.Element(parent_tag)
    for _, row in df.iterrows():
        tag = str(row.get('Etiqueta', '')).strip()
        if not tag or tag.lower() == 'nan': continue
        
        child = etree.SubElement(root, tag)
        val = str(row.get('Valor_Texto', '')).strip()
        if val and val.lower() != 'nan':
            child.text = val
            
        for col in df.columns:
            if col.startswith('@'):
                attr_val = str(row.get(col, '')).strip()
                if attr_val and attr_val.lower() != 'nan':
                    child.set(col[1:], attr_val)
            elif col == 'Sub_Nodos_Ocultos':
                sub_val = str(row.get(col, '')).strip()
                if sub_val and sub_val.lower() != 'nan':
                    try:
                        # Extraer hijos de la etiqueta sintética
                        sub_tree = etree.fromstring(sub_val)
                        for sub_c in sub_tree: child.append(sub_c)
                    except: pass
    return etree.tostring(root, encoding='unicode')

# --- LÓGICA RECURSIVA PARA TABLAS ANIDADAS INFINITAS ---
def renderizar_anidados(xml_string, id_unico, titulo_padre, callback_guardar):
    try:
        parent_tag, df_sub = xml_to_df(xml_string)
        df_render = df_sub.copy()
        
        if "🔍 Ver Anidados" not in df_render.columns:
            df_render.insert(0, "🔍 Ver Anidados", False)
            
        st.markdown(f"#### ↳ Nivel Anidado: `{titulo_padre}`")
        
        df_editado = st.data_editor(
            df_render,
            key=f"editor_{id_unico}",
            use_container_width=True,
            hide_index=True,
            column_config={"🔍 Ver Anidados": st.column_config.CheckboxColumn("🔍 Ver Anidados", default=False)}
        )
        
        # Guardar si hubo cambios en los datos (ignorando la columna del checkbox)
        df_para_comprobar = df_editado.drop(columns=["🔍 Ver Anidados"])
        if not df_para_comprobar.equals(df_sub):
            nuevo_xml = df_to_xml(parent_tag, df_para_comprobar)
            callback_guardar(nuevo_xml)
            st.rerun()
            
        # Llamada recursiva si el usuario quiere ver los sub-hijos de este nivel
        filas_marcadas = df_editado[df_editado["🔍 Ver Anidados"] == True]
        if not filas_marcadas.empty:
            idx = filas_marcadas.index[0]
            # Detectamos si la fila seleccionada tiene contenido complejo
            cols_complejas = [c for c in df_para_comprobar.columns if isinstance(df_para_comprobar.at[idx, c], str) and df_para_comprobar.at[idx, c].strip().startswith('<')]
            
            if cols_complejas:
                with st.container(border=True):
                    for col_name in cols_complejas:
                        sub_str = str(df_para_comprobar.at[idx, col_name])
                        
                        # Definimos cómo el hijo avisa al padre de un cambio
                        def crear_callback(r_idx=idx, r_col=col_name):
                            def cb(nuevo_sub):
                                df_actualizado = df_para_comprobar.copy()
                                df_actualizado.at[r_idx, r_col] = nuevo_sub
                                xml_padre = df_to_xml(parent_tag, df_actualizado)
                                callback_guardar(xml_padre)
                            return cb
                            
                        renderizar_anidados(sub_str, f"{id_unico}_{idx}_{col_name}", col_name, crear_callback())
    except Exception as e:
        st.error(f"Error renderizando el anidado {titulo_padre}: {e}")

# --- AYUDA MODAL ---
@st.dialog("📖 Guía de Uso del Gestor GHC")
def mostrar_ayuda():
    st.markdown("""
    **Navegación General**
    *   Usa la barra lateral izquierda para moverte entre las diferentes estructuras (Marcos de horario, Aulas, Sesiones, etc.).
    
    **Edición y Guardado**
    *   Haz doble clic sobre cualquier celda de la tabla para editar su valor.
    *   Las agrupaciones de datos (como varias aulas en un *Conjunto de Aulas*) se muestran separadas por comas (ej. `12, 14, B4`). Edítalas manteniendo ese formato.
    *   Los cambios se **guardan solos en la nube** al hacer clic fuera de la celda.
    
    **El Checkbox "🔍 Ver Anidados"**
    *   Si ves columnas con texto técnico como `<Nodos_...>`, significa que hay sub-elementos complejos ahí dentro.
    *   Marca el checkbox **🔍 Ver Anidados** a la izquierda de la fila y se desplegará una nueva tabla debajo para editar esos componentes de forma cómoda. ¡Puedes hacerlo tantas veces como quieras hacia adentro!
    
    **Filtros Visuales**
    *   Para limpiar la pantalla de datos técnicos (como `@clavX` o `@id`), desmarca sus casillas en el menú lateral.
    
    **Descarga**
    *   Pulsa **📥 Descargar PLANIFICADOR.XML** para generar tu archivo compatible de vuelta a la aplicación de Peñalara.
    """)

# --- INICIO DE SESIÓN ---
if "bd_cargada" not in st.session_state:
    st.session_state.xml_tree, st.session_state.data_frames = cargar_datos_de_nube()
    st.session_state.bd_cargada = True

# --- PANTALLA DE CARGA INICIAL (PARSER PRINCIPAL) ---
if st.session_state.xml_tree is None:
    st.info("☁️ La base de datos central está vacía. Sube tu archivo XML de Peñalara.")
    uploaded_file = st.file_uploader("📂 Sube tu 'planificador.xml'", type=["xml"])
    
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
                    fila = {'ID_SISTEMA': item.get('id') or item.findtext('nombre') or item.get('nombre') or f"Elemento_{i}"}
                    for k, v in item.attrib.items(): 
                        fila[f"@{k}"] = v
                        
                    hijos_agrupados = {}
                    for child in item:
                        # Reglas heurísticas de GHC para listas planas
                        if child.tag in ['listaDeAulas', 'otrasAulas']: 
                            val = ", ".join([c.text for c in child.findall('aula') if c.text])
                        elif child.tag == 'otrosProfesores': 
                            val = ", ".join([c.text for c in child.findall('profesor') if c.text])
                        elif child.tag == 'otrosGrupos': 
                            val = ", ".join([c.text for c in child.findall('grupo') if c.text])
                        else:
                            # Extracción general segura
                            val = child.get('clavX') or child.get('id') or child.text
                            if not val and child.attrib:
                                val = list(child.attrib.values())[0]
                                
                            # Si es un nodo muy complejo (con hijos)
                            if not val and len(child) > 0:
                                val = etree.tostring(child, encoding='unicode')
                            elif not val:
                                val = ""
                        
                        # Acumular etiquetas repetidas sin sobrescribirlas (Solución al corte de aulas)
                        if child.tag in hijos_agrupados:
                            hijos_agrupados[child.tag].append(str(val))
                        else:
                            hijos_agrupados[child.tag] = [str(val)]
                    
                    # Convertir los grupos acumulados al diccionario final de Pandas
                    for tag, lista_vals in hijos_agrupados.items():
                        if all(v.startswith('<') for v in lista_vals if v):
                            # Si todos son complejos, envolverlos para el renderizador anidado
                            fila[tag] = f"<Nodos_{tag}>" + "".join(lista_vals) + f"</Nodos_{tag}>"
                        else:
                            # Aplanar listas en comas para fácil lectura (12, 14, B4)
                            fila[tag] = ", ".join([v for v in lista_vals if v])
                            
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

# --- INTERFAZ PRINCIPAL ---
if st.session_state.xml_tree is not None:
    # --- BARRA LATERAL (NAVEGACIÓN Y AYUDA) ---
    st.sidebar.markdown("### 🗺️ Navegación")
    
    if st.sidebar.button("❓ Guía de Uso", type="primary"):
        mostrar_ayuda()
        
    st.sidebar.divider()
    
    tab_names = list(st.session_state.data_frames.keys())
    selected_tab = st.sidebar.radio("Sección actual:", tab_names)
    
    st.sidebar.divider()
    
    st.sidebar.markdown("### 👁️ Filtro de Atributos del XML")
    st.sidebar.caption("Selecciona qué columnas clave deseas visualizar:")
    
    ver_nombre = st.sidebar.checkbox("Nombre completo", value=True)
    ver_abreviatura = st.sidebar.checkbox("Abreviatura", value=True)
    ver_identificador = st.sidebar.checkbox("Identificador (@id)", value=True)
    ver_clave_externa = st.sidebar.checkbox("Clave externa (@clavX)", value=True)
    
    st.sidebar.divider()
    if st.sidebar.button("🚨 Reiniciar Base de Datos", type="secondary"):
        db.collection('ghc_sistema').document('plantilla_base').delete()
        st.session_state.xml_tree = None
        st.rerun()
        
    st.sidebar.divider()
    st.sidebar.markdown("### 💾 Exportación")
    btn_descarga = st.sidebar.empty()

    # --- TABLA PRINCIPAL ---
    df_original = st.session_state.data_frames[selected_tab]
    columnas_a_ocultar = []
    
    if not ver_identificador: columnas_a_ocultar.extend(['@id', 'id'])
    if not ver_nombre: columnas_a_ocultar.extend(['@nombre', 'nombre'])
    if not ver_abreviatura: columnas_a_ocultar.extend(['@abreviatura', 'abreviatura', '@abrev', 'abrev'])
    if not ver_clave_externa: columnas_a_ocultar.extend(['@claveX', 'claveX', '@claveExterna', 'claveExterna', '@clavX', 'clavX'])

    st.markdown(f"### 📋 Tabla de {selected_tab}")
    
    df_interfaz = df_original.copy()
    if "🔍 Ver Anidados" not in df_interfaz.columns: 
        df_interfaz.insert(0, "🔍 Ver Anidados", False)
        
    columnas_existentes_a_ocultar = [col for col in columnas_a_ocultar if col in df_interfaz.columns]
    df_interfaz_filtrada = df_interfaz.drop(columns=columnas_existentes_a_ocultar)
    
    df_editado_filtrado = st.data_editor(
        df_interfaz_filtrada, 
        use_container_width=True, 
        hide_index=True, 
        key=f"editor_{selected_tab}",
        column_config={"🔍 Ver Anidados": st.column_config.CheckboxColumn("🔍 Ver Anidados", help="Abre las sub-tablas", default=False)}
    )
    
    # Procesar guardado principal
    df_para_guardar = df_original.copy()
    for col in df_editado_filtrado.columns:
        if col != "🔍 Ver Anidados" and col in df_para_guardar.columns:
            df_para_guardar[col] = df_editado_filtrado[col]
            
    if not df_para_guardar.equals(df_original):
        st.session_state.data_frames[selected_tab] = df_para_guardar
        guardar_tabla_en_nube(selected_tab, df_para_guardar)
        st.toast('☁️ ¡Cambio guardado en la nube!')

    # --- LÓGICA DE DETECCIÓN DE ANIDADOS (Conecta con la recursividad) ---
    filas_marcadas = df_editado_filtrado[df_editado_filtrado["🔍 Ver Anidados"] == True]
    
    if not filas_marcadas.empty:
        idx = filas_marcadas.index[0]
        id_elemento = df_para_guardar.at[idx, 'ID_SISTEMA']
        campos_complejos = [col for col in df_para_guardar.columns if isinstance(df_para_guardar.at[idx, col], str) and str(df_para_guardar.at[idx, col]).strip().startswith('<')]
        
        st.divider()
        st.markdown(f"### 🗂️ Sub-Tablas de: `{id_elemento}`")
        
        if not campos_complejos:
            st.info("Este elemento no tiene plantillas, restricciones ni datos anidados complejos.")
        else:
            for col_name in campos_complejos:
                xml_string = str(df_para_guardar.at[idx, col_name])
                
                # Definir Callback para el nivel 0
                def callback_nivel_cero(nuevo_xml, index_padre=idx, columna_padre=col_name):
                    st.session_state.data_frames[selected_tab].at[index_padre, columna_padre] = nuevo_xml
                    guardar_tabla_en_nube(selected_tab, st.session_state.data_frames[selected_tab])
                    st.toast("☁️ ¡Sub-tabla guardada en la nube!")
                
                renderizar_anidados(xml_string, f"sub_{selected_tab}_{idx}_{col_name}", col_name, callback_nivel_cero)

    # --- EXPORTACIÓN REPARADA (Soporte Multi-Aula/Multi-Tramos) ---
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
                    
                    if col.startswith('@'): 
                        nodo.set(col[1:], valor_str)
                        
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
                        # Extraer hermanos previos del XML base para inferir la estructura
                        nodos_hermanos = nodo.findall(col)
                        
                        # Inserciones complejas (reconstrucción de Sub_Nodos)
                        if isinstance(valor_str, str) and valor_str.startswith('<') and valor_str.endswith('>'):
                            try:
                                nuevo_hijo = etree.fromstring(valor_str)
                                for old in nodos_hermanos: nodo.remove(old)
                                
                                # Si es un Wrapper sintético generado por nosotros, extraer sus hijos
                                if nuevo_hijo.tag.startswith('Nodos_'):
                                    for child_node in nuevo_hijo: nodo.append(child_node)
                                else:
                                    nodo.append(nuevo_hijo)
                                continue
                            except: pass
                            
                        # Limpiar antes de re-insertar
                        for n in nodos_hermanos: nodo.remove(n)
                        
                        # Separación de elementos iterativos (ej. Aulas de un conjunto)
                        # No restringimos con .isdigit() para permitir IDs como "B4", "Música"
                        list_tags_frecuentes = ['aula', 'profesor', 'grupo', 'materia', 'tramo', 'sesion']
                        es_multi = (len(nodos_hermanos) > 1) or (col in list_tags_frecuentes)
                        
                        if es_multi and "," in valor_str:
                            for v in valor_str.split(','):
                                v_limpio = v.strip()
                                if v_limpio:
                                    nuevo_elem = etree.SubElement(nodo, col)
                                    # Conservar la lógica original de GHC: priorizar clavX
                                    if nodos_hermanos and nodos_hermanos[0].get('clavX'):
                                        nuevo_elem.set('clavX', v_limpio)
                                    elif nodos_hermanos and nodos_hermanos[0].get('id'):
                                        nuevo_elem.set('id', v_limpio)
                                    else:
                                        nuevo_elem.set('clavX', v_limpio)
                        elif valor_str: 
                            nuevo_elem = etree.SubElement(nodo, col)
                            if nodos_hermanos and nodos_hermanos[0].get('clavX'):
                                nuevo_elem.set('clavX', valor_str)
                            else:
                                nuevo_elem.text = valor_str

    xml_str = etree.tostring(root, encoding='ISO-8859-1', xml_declaration=True, pretty_print=True)
    with btn_descarga:
        st.download_button(label="📥 DESCARGAR PLANIFICADOR.XML", data=xml_str, file_name="PLANIFICADOR_NUBE.xml", mime="application/xml", use_container_width=True)
