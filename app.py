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

# --- CONVERSORES DE XML A SUB-TABLAS ---
def xml_to_df(xml_str):
    tree = etree.fromstring(xml_str)
    parent_tag = tree.tag
    rows = []
    for child in tree:
        row = {'Etiqueta': child.tag, 'Valor_Texto': child.text if child.text else ""}
        for k, v in child.attrib.items():
            row[f"@{k}"] = v
        if len(child) > 0:
            row['Sub_Nodos_Ocultos'] = "".join([etree.tostring(c, encoding='unicode') for c in child])
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
                        sub_tree = etree.fromstring(f"<wrapper>{sub_val}</wrapper>")
                        for sub_c in sub_tree: child.append(sub_c)
                    except: pass
    return etree.tostring(root, encoding='unicode')

# --- INICIO DE SESIÓN ---
if "bd_cargada" not in st.session_state:
    st.session_state.xml_tree, st.session_state.data_frames = cargar_datos_de_nube()
    st.session_state.bd_cargada = True

# --- PANTALLA DE CARGA ---
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
                        
                    # LÓGICA CORREGIDA: Agrupar etiquetas repetidas (como <aula>) para no sobrescribirlas
                    hijos_agrupados = {}
                    
                    for child in item:
                        if child.tag in ['listaDeAulas', 'otrasAulas']: 
                            fila[child.tag] = ", ".join([c.text for c in child.findall('aula') if c.text])
                        elif child.tag == 'otrosProfesores': 
                            fila[child.tag] = ", ".join([c.text for c in child.findall('profesor') if c.text])
                        elif child.tag == 'otrosGrupos': 
                            fila[child.tag] = ", ".join([c.text for c in child.findall('grupo') if c.text])
                        else:
                            # Extraer el valor dependiendo de si es texto, atributo o subnodo
                            if len(child) == 0 and not child.attrib: 
                                val = child.text.strip() if child.text else ""
                            else:
                                val = child.get('clavX') or child.get('id') or child.text
                                if not val and len(child) > 0:
                                    val = etree.tostring(child, encoding='unicode')
                                elif not val:
                                    val = ""
                            
                            # Si la etiqueta ya existe en el diccionario, la añadimos a una lista
                            if child.tag in hijos_agrupados:
                                hijos_agrupados[child.tag].append(str(val))
                            else:
                                hijos_agrupados[child.tag] = [str(val)]
                    
                    # Convertimos las listas agrupadas en strings separados por comas
                    for tag, lista_vals in hijos_agrupados.items():
                        # Si solo hay uno, extrae el valor; si hay varios, únelos con comas
                        fila[tag] = ", ".join([v for v in lista_vals if v]) if len(lista_vals) > 1 else lista_vals[0]
                        
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
    # --- BARRA LATERAL (NAVEGACIÓN Y CONFIGURACIÓN UX) ---
    st.sidebar.markdown("### 🗺️ Navegación")
    tab_names = list(st.session_state.data_frames.keys())
    selected_tab = st.sidebar.radio("Sección actual:", tab_names)
    
    st.sidebar.divider()
    
    # MEJORA UX: Cuadro de configuración de visibilidad de atributos solicitados
    st.sidebar.markdown("### 👁️ Filtro de Atributos del XML")
    st.sidebar.caption("Selecciona qué columnas clave deseas visualizar en la tabla principal:")
    
    ver_nombre = st.sidebar.checkbox("Nombre completo (@nombre / nombre)", value=True)
    ver_abreviatura = st.sidebar.checkbox("Abreviatura (@abreviatura / abreviatura)", value=True)
    ver_identificador = st.sidebar.checkbox("Identificador (@id)", value=True)
    ver_clave_externa = st.sidebar.checkbox("Clave externa (@claveX / claveExterna)", value=True)
    
    st.sidebar.divider()
    if st.sidebar.button("🚨 Reiniciar Base de Datos", type="secondary"):
        db.collection('ghc_sistema').document('plantilla_base').delete()
        st.session_state.xml_tree = None
        st.rerun()
        
    st.sidebar.divider()
    st.sidebar.markdown("### 💾 Exportación")
    btn_descarga = st.sidebar.empty()

    # --- PROCESADO DE VISIBILIDAD DE COLUMNAS ---
    df_original = st.session_state.data_frames[selected_tab]
    
    # Creamos una lista de columnas que el usuario quiere forzar a OCULTAR si desmarca los checks
    columnas_a_ocultar = []
    
    # Peñalara suele guardar estos atributos con '@' por ser atributos XML en nuestro DataFrame
    if not ver_identificador: 
        columnas_a_ocultar.extend(['@id', 'id'])
    if not ver_nombre: 
        columnas_a_ocultar.extend(['@nombre', 'nombre'])
    if not ver_abreviatura: 
        columnas_a_ocultar.extend(['@abreviatura', 'abreviatura', '@abrev', 'abrev'])
    if not ver_clave_externa: 
        columnas_a_ocultar.extend(['@claveX', 'claveX', '@claveExterna', 'claveExterna', '@clavX', 'clavX'])

    # --- TABLA PRINCIPAL ---
    st.markdown(f"### 📋 Tabla de {selected_tab}")
    st.caption("Filtra las columnas visibles desde el panel lateral izquierdo según tus necesidades de edición.")
    
    # Construimos la interfaz visual aplicando los filtros aplicados
    df_interfaz = df_original.copy()
    if "🔍 Ver Anidados" not in df_interfaz.columns: 
        df_interfaz.insert(0, "🔍 Ver Anidados", False)
        
    # Filtrar activamente eliminando las columnas seleccionadas en el sidebar (si existen en esta tabla)
    columnas_existentes_a_ocultar = [col for col in columnas_a_ocultar if col in df_interfaz.columns]
    df_interfaz_filtrada = df_interfaz.drop(columns=columnas_existentes_a_ocultar)
    
    # Dibujamos el data_editor con las columnas ya limpias
    df_editado_filtrado = st.data_editor(
        df_interfaz_filtrada, 
        use_container_width=True, 
        hide_index=True, 
        key=f"editor_{selected_tab}",
        column_config={"🔍 Ver Anidados": st.column_config.CheckboxColumn("🔍 Ver Anidados", help="Abre las sub-tablas", default=False)}
    )
    
    # RECONSTRUCCIÓN: Como la tabla editada puede no tener todas las columnas (porque se ocultaron), 
    # volvemos a fusionar los cambios editados sobre el dataframe original para no perder las columnas ocultas.
    df_para_guardar = df_original.copy()
    
    # Actualizar solo las celdas de las columnas que estaban visibles y que el usuario pudo haber modificado
    for col in df_editado_filtrado.columns:
        if col != "🔍 Ver Anidados" and col in df_para_guardar.columns:
            df_para_guardar[col] = df_editado_filtrado[col]
            
    # Comprobar si ha cambiado algo realmente para subirlo a Firebase
    if not df_para_guardar.equals(df_original):
        st.session_state.data_frames[selected_tab] = df_para_guardar
        guardar_tabla_en_nube(selected_tab, df_para_guardar)
        st.toast('☁️ ¡Cambio guardado en la nube!')

    # --- LÓGICA DE SUB-TABLAS (DATOS ANIDADOS) ---
    # Detectamos la fila seleccionada usando la tabla editada
    filas_marcadas = df_editado_filtrado[df_editado_filtrado["🔍 Ver Anidados"] == True]
    
    if not filas_marcadas.empty:
        idx = filas_marcadas.index[0]
        id_elemento = df_para_guardar.at[idx, 'ID_SISTEMA']
        
        campos_complejos = [col for col in df_para_guardar.columns if isinstance(df_para_guardar.at[idx, col], str) and str(df_para_guardar.at[idx, col]).strip().startswith('<')]
        
        st.divider()
        st.markdown(f"### 🗂️ Sub-Tablas de: `{id_elemento}`")
        
        if not campos_complejos:
            st.info("Este elemento no tiene plantillas, restricciones ni datos aninados complejos.")
        else:
            for col_name in campos_complejos:
                st.markdown(f"#### ↳ Estructura Anidada: `{col_name}`")
                xml_string = str(df_para_guardar.at[idx, col_name])
                
                try:
                    parent_tag, df_sub = xml_to_df(xml_string)
                    df_sub_editado = st.data_editor(
                        df_sub, 
                        key=f"sub_{selected_tab}_{idx}_{col_name}", 
                        num_rows="dynamic", 
                        use_container_width=True
                    )
                    
                    if not df_sub_editado.equals(df_sub):
                        nuevo_xml = df_to_xml(parent_tag, df_sub_editado)
                        st.session_state.data_frames[selected_tab].at[idx, col_name] = nuevo_xml
                        guardar_tabla_en_nube(selected_tab, st.session_state.data_frames[selected_tab])
                        st.toast("☁️ ¡Sub-tabla guardada!")
                        st.rerun()
                        
                except Exception as e:
                    st.error(f"Error en sub-tabla {col_name}: {e}")

    # --- EXPORTACIÓN PEÑALARA ---
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
                        hijo = nodo.find(col)
                        
                        # Manejo de estructuras anidadas complejas
                        if isinstance(valor_str, str) and valor_str.startswith('<') and valor_str.endswith('>'):
                            try:
                                nuevo_hijo = etree.fromstring(valor_str)
                                if hijo is not None: nodo.replace(hijo, nuevo_hijo)
                                else: nodo.append(nuevo_hijo)
                                continue
                            except: pass
                            
                        # LÓGICA CORREGIDA PARA EXPORTACIÓN DE LISTAS (como las de aulas que ahora están agrupadas por comas)
                        if hijo is not None:
                            # Limpiamos todos los hijos que se llamen igual (ej. limpiamos las <aula> viejas)
                            nodos_hermanos = nodo.findall(col)
                            for n in nodos_hermanos:
                                nodo.remove(n)
                                
                            # Si hay comas, creamos varios nodos
                            if "," in valor_str and valor_str.replace(",", "").strip().isdigit():
                                for v in valor_str.split(','):
                                    if v.strip(): 
                                        nuevo_elem = etree.SubElement(nodo, col)
                                        # Asignamos al atributo clavX (típico de Peñalara)
                                        nuevo_elem.set('clavX', v.strip())
                            else:
                                nuevo_elem = etree.SubElement(nodo, col)
                                nuevo_elem.text = valor_str
                                
                        elif valor_str: 
                            if "," in valor_str and valor_str.replace(",", "").strip().isdigit():
                                for v in valor_str.split(','):
                                    if v.strip():
                                        nuevo_elem = etree.SubElement(nodo, col)
                                        nuevo_elem.set('clavX', v.strip())
                            else:
                                etree.SubElement(nodo, col).text = valor_str

    xml_str = etree.tostring(root, encoding='ISO-8859-1', xml_declaration=True, pretty_print=True)
    with btn_descarga:
        st.download_button(label="📥 DESCARGAR PLANIFICADOR.XML", data=xml_str, file_name="PLANIFICADOR_NUBE.xml", mime="application/xml", use_container_width=True)
