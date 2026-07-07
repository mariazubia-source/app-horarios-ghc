import streamlit as st
import pandas as pd
from lxml import etree
import io

# Configuración de la pantalla visual
st.set_page_config(page_title="Editor GHC", layout="wide")
st.title("🎓 Gestor Visual de Horarios - Peñalara GHC")

# Memoria de la aplicación
if "xml_tree" not in st.session_state:
    st.session_state.xml_tree = None
    st.session_state.data_frames = {}

uploaded_file = st.file_uploader("📂 Sube tu archivo 'planificador.xml' (Fase 1: Memoria Temporal)", type=["xml"])

if uploaded_file is not None and st.session_state.xml_tree is None:
    parser = etree.XMLParser(encoding='iso-8859-1', strip_cdata=False)
    tree = etree.parse(uploaded_file, parser)
    root = tree.getroot()
    st.session_state.xml_tree = tree
    
    dfs = {}
    for container in root:
        if len(container) > 0:
            tag_hijo = container[0].tag
            registros = []
            for i, item in enumerate(container.findall(tag_hijo)):
                fila = {}
                # SUPER RADAR: Captura ID, nombre, abreviatura, o inventa uno si Peñalara lo oculta
                identificador = item.get('id') or item.findtext('nombre') or item.get('nombre') or item.get('abreviatura') or f"Elemento_{i}"
                fila['ID_SISTEMA'] = identificador
                
                for k, v in item.attrib.items():
                    fila[f"@{k}"] = v
                for child in item:
                    if child.tag in ['listaDeAulas', 'otrasAulas']:
                        fila[child.tag] = ", ".join([c.text for c in child.findall('aula') if c.text])
                    elif child.tag == 'otrosProfesores':
                        fila[child.tag] = ", ".join([c.text for c in child.findall('profesor') if c.text])
                    elif child.tag == 'otrosGrupos':
                        fila[child.tag] = ", ".join([c.text for c in child.findall('grupo') if c.text])
                    elif len(child) == 0:
                        fila[child.tag] = child.text.strip() if child.text else ""
                    else:
                        inner_xml = (child.text or '') + ''.join([etree.tostring(c, encoding='unicode') for c in child])
                        fila[child.tag] = inner_xml.strip()
                registros.append(fila)
            if registros:
                dfs[container.tag.capitalize()] = pd.DataFrame(registros).fillna("")
    
    st.session_state.data_frames = dfs
    st.rerun()

if st.session_state.xml_tree is not None:
    st.success("✅ Datos cargados correctamente. Entorno de edición visual activado.")
    
    tab_names = list(st.session_state.data_frames.keys())
    tabs = st.tabs(tab_names)
    
    for i, tab in enumerate(tabs):
        with tab:
            df = st.session_state.data_frames[tab_names[i]]
            if df.empty: continue
            
            # PANTALLA DIVIDIDA
            col1, col2 = st.columns([1.2, 1])
            
            with col1:
                st.markdown("### 📋 Tabla General (Solo lectura)")
                st.dataframe(df, use_container_width=True, hide_index=True)
            
            with col2:
                st.markdown("### 🛠️ Panel de Edición Detallada")
                opciones = df['ID_SISTEMA'].tolist()
                seleccion = st.selectbox("Selecciona qué elemento quieres editar:", ["-- Elige uno --"] + opciones, key=f"sel_{i}")
                
                if seleccion != "-- Elige uno --":
                    fila_actual = df[df['ID_SISTEMA'] == seleccion].iloc[0]
                    with st.form(key=f"form_{i}_{seleccion}"):
                        nuevos_valores = {}
                        for col_name in df.columns:
                            if col_name == 'ID_SISTEMA': continue
                            valor_actual = str(fila_actual[col_name])
                            
                            # Cajas grandes para etiquetas complejas, pequeñas para textos cortos
                            if "<" in valor_actual and ">" in valor_actual:
                                nuevos_valores[col_name] = st.text_area(f"🔧 {col_name} (Avanzado)", value=valor_actual, height=150)
                            else:
                                nuevos_valores[col_name] = st.text_input(f"📄 {col_name}", value=valor_actual)
                        
                        if st.form_submit_button("💾 Guardar Cambios en Memoria", type="primary"):
                            idx = df[df['ID_SISTEMA'] == seleccion].index[0]
                            for k, v in nuevos_valores.items():
                                st.session_state.data_frames[tab_names[i]].at[idx, k] = v
                            st.rerun()

    st.divider()
    
    # Exportación (Necesario hasta que integremos la Base de Datos en la Fase 2)
    if st.button("📦 DESCARGAR XML FINAL PARA PEÑALARA", type="secondary"):
        root = st.session_state.xml_tree.getroot()
        for tab_name, df_editado in st.session_state.data_frames.items():
            nombre_pestana = tab_name.lower()
            container = root.find(nombre_pestana)
            if container is None: continue
            tag_hijo = container[0].tag
            datos = df_editado.to_dict('records')
            
            for fila in datos:
                id_sistema = str(fila.get('ID_SISTEMA', ''))
                if not id_sistema or id_sistema.startswith("Elemento_"): continue
                
                nodo = container.find(f"{tag_hijo}[@id='{id_sistema}']")
                if nodo is None: nodo = container.find(f"{tag_hijo}[nombre='{id_sistema}']")
                if nodo is None: nodo = container.find(f"{tag_hijo}[@nombre='{id_sistema}']")
                if nodo is None: nodo = container.find(f"{tag_hijo}[@abreviatura='{id_sistema}']")
                    
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
                        elif col == 'otrosProfesores':
                            lista_nodo = nodo.find(col)
                            if lista_nodo is None and valor_str: lista_nodo = etree.SubElement(nodo, col)
                            if lista_nodo is not None:
                                for c in list(lista_nodo): lista_nodo.remove(c)
                                for p in valor_str.split(','):
                                    if p.strip(): etree.SubElement(lista_nodo, 'profesor').text = p.strip()
                        elif col == 'otrosGrupos':
                            lista_nodo = nodo.find(col)
                            if lista_nodo is None and valor_str: lista_nodo = etree.SubElement(nodo, col)
                            if lista_nodo is not None:
                                for c in list(lista_nodo): lista_nodo.remove(c)
                                for g in valor_str.split(','):
                                    if g.strip(): etree.SubElement(lista_nodo, 'grupo').text = g.strip()
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
                            elif valor_str:
                                etree.SubElement(nodo, col).text = valor_str

        xml_str = etree.tostring(root, encoding='ISO-8859-1', xml_declaration=True, pretty_print=True)
        st.download_button(label="📥 HAZ CLIC AQUÍ PARA DESCARGAR EL ARCHIVO", data=xml_str, file_name="PLANIFICADOR_ACTUALIZADO.xml", mime="application/xml")
