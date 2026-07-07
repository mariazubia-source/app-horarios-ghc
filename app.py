import streamlit as st
import pandas as pd
from lxml import etree
import io

# Configuración de la pantalla visual
st.set_page_config(page_title="Editor GHC", layout="wide")
st.title("🎓 Gestor Visual de Horarios - Peñalara GHC")
st.markdown("Sube tu archivo XML. La aplicación leerá absolutamente todos los datos anidados para que puedas editarlos cómodamente.")

# Variables de memoria para no perder datos al hacer clics
if "xml_tree" not in st.session_state:
    st.session_state.xml_tree = None
    st.session_state.data_frames = {}

# Botón de subida
uploaded_file = st.file_uploader("📂 Sube tu archivo 'planificador.xml'", type=["xml"])

if uploaded_file is not None and st.session_state.xml_tree is None:
    # Leer el archivo con el motor potente para XML
    parser = etree.XMLParser(encoding='iso-8859-1', strip_cdata=False)
    tree = etree.parse(uploaded_file, parser)
    root = tree.getroot()
    st.session_state.xml_tree = tree
    
    # Extraer todos los datos (incluso los anidados)
    dfs = {}
    for container in root:
        if len(container) > 0:
            tag_hijo = container[0].tag
            registros = []
            for item in container.findall(tag_hijo):
                fila = {}
                identificador = item.get('id') or item.findtext('nombre') or item.get('nombre')
                if not identificador: continue
                fila['ID_SISTEMA (NO TOCAR)'] = identificador
                
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
                        # Si es un nodo muy complejo (constricciones), guardar su código interno
                        inner_xml = (child.text or '') + ''.join([etree.tostring(c, encoding='unicode') for c in child])
                        fila[child.tag] = inner_xml.strip()
                registros.append(fila)
            if registros:
                dfs[container.tag.capitalize()] = pd.DataFrame(registros).fillna("")
    
    st.session_state.data_frames = dfs
    st.rerun()

# Si el archivo está cargado, mostrar la interfaz visual
if st.session_state.xml_tree is not None:
    st.success("✅ ¡Base de datos cargada al 100%! Modifica lo que necesites en las pestañas de abajo.")
    
    # Crear pestañas interactivas (Tabs)
    tab_names = list(st.session_state.data_frames.keys())
    tabs = st.tabs(tab_names)
    
    edited_dfs = {}
    for i, tab in enumerate(tabs):
        with tab:
            df = st.session_state.data_frames[tab_names[i]]
            # Crear la tabla visual editable
            edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic", key=f"editor_{i}")
            edited_dfs[tab_names[i]] = edited_df
            
    st.divider()
    
    # Botón mágico para reconstruir y exportar
    if st.button("🔨 GUARDAR CAMBIOS Y GENERAR XML", type="primary"):
        root = st.session_state.xml_tree.getroot()
        for tab_name, df_editado in edited_dfs.items():
            nombre_pestana = tab_name.lower()
            container = root.find(nombre_pestana)
            if container is None: continue
            tag_hijo = container[0].tag
            datos = df_editado.to_dict('records')
            
            for fila in datos:
                id_sistema = str(fila.get('ID_SISTEMA (NO TOCAR)', ''))
                if not id_sistema: continue
                
                nodo = container.find(f"{tag_hijo}[@id='{id_sistema}']")
                if nodo is None: nodo = container.find(f"{tag_hijo}[nombre='{id_sistema}']")
                if nodo is None: nodo = container.find(f"{tag_hijo}[@nombre='{id_sistema}']")
                    
                if nodo is not None:
                    for col, valor in fila.items():
                        if col == 'ID_SISTEMA (NO TOCAR)': continue
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

        # Descarga final
        xml_str = etree.tostring(root, encoding='ISO-8859-1', xml_declaration=True, pretty_print=True)
        st.download_button(label="📥 DESCARGAR PLANIFICADOR ACTUALIZADO", data=xml_str, file_name="PLANIFICADOR_ACTUALIZADO.xml", mime="application/xml")
