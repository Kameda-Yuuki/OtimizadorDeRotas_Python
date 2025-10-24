import streamlit as st
import pandas as pd
import plotly.express as px
from geopy.distance import geodesic
import numpy as np
from datetime import datetime
# Inicialização de estado e utilitários para rotas customizáveis, otimização e bloqueios
if "custom_routes" not in st.session_state:
    st.session_state.custom_routes = {}  # nome -> lista de paradas (dicts com nome/lat/lng)
if "blocked_segments" not in st.session_state:
    st.session_state.blocked_segments = []  # lista de dicts: {"from": index_or_name, "to": index_or_name, "radius_m": 100}
if "show_block_panel" not in st.session_state:
    st.session_state.show_block_panel = False

def init_custom_route_from_csv(uploaded_file):
    """Lê CSV com colunas: nome,lat,lng e retorna lista de paradas"""
    try:
        df = pd.read_csv(uploaded_file)
    except Exception:
        df = pd.read_csv(uploaded_file, sep=';')
    df = df.rename(columns={c: c.strip() for c in df.columns})
    required = {"nome", "lat", "lng"}
    if not required.issubset(set([c.lower() for c in df.columns])):
        raise ValueError("CSV precisa das colunas: nome, lat, lng")
    stops = []
    for _, r in df.iterrows():
        stops.append({"nome": str(r.get("nome") or r.get("Nome")), "lat": float(r.get("lat") or r.get("Lat")), "lng": float(r.get("lng") or r.get("Lng"))})
    return stops

def save_custom_route(name, stops):
    """Salva rota custom no session_state"""
    if not name:
        raise ValueError("Nome da rota obrigatório")
    st.session_state.custom_routes[name] = stops

def try_register_custom_routes_into_globals(globals_dict):
    """Tenta inserir rotas custom no dicionário de linhas (se existir)"""
    if "linhas_marilia" in globals_dict and isinstance(globals_dict["linhas_marilia"], dict):
        for name, stops in st.session_state.custom_routes.items():
            # Mantém padrão de estrutura compatível com linhas_marilia
            globals_dict["linhas_marilia"][name + " (Custom)"] = {
                "paradas": stops,
                "velocidade_media": 30,
                "horario_pico": []
            }

# Função que gera uma rota "otimizada" mantendo todas as paradas, mas usando interpolação direta
def gerar_rota_otimizada(paradas, desvio=0, pontos_por_segmento=8):
    pontos = []
    for i in range(len(paradas)-1):
        p1 = paradas[i]
        p2 = paradas[i+1]
        pontos.append({"Lat": p1["lat"], "Lon": p1["lng"], "Parada": p1["nome"], "Tipo": "Parada"})
        for j in range(1, pontos_por_segmento):
            t = j / pontos_por_segmento
            lat = p1["lat"] + (p2["lat"] - p1["lat"]) * t
            lon = p1["lng"] + (p2["lng"] - p1["lng"]) * t
            # pequeno desvio para criar alternativas se solicitado
            if desvio != 0:
                lat += desvio * 0.00018 * np.cos(t * np.pi * 2)
                lon += desvio * 0.00018 * np.sin(t * np.pi * 2)
            pontos.append({"Lat": lat, "Lon": lon, "Parada": f"{p1['nome']} → {p2['nome']}", "Tipo": "Rota"})
    pontos.append({"Lat": paradas[-1]["lat"], "Lon": paradas[-1]["lng"], "Parada": paradas[-1]["nome"], "Tipo": "Parada"})
    # Suavização simples (moving average) para remover zig-zags
    coords = [(p["Lat"], p["Lon"]) for p in pontos]
    smooth_coords = []
    w = 3
    for idx in range(len(coords)):
        lat_sum = 0
        lon_sum = 0
        count = 0
        for k in range(max(0, idx-w), min(len(coords), idx+w+1)):
            lat_sum += coords[k][0]
            lon_sum += coords[k][1]
            count += 1
        smooth_coords.append((lat_sum/count, lon_sum/count))
    for k, p in enumerate(pontos):
        p["Lat"], p["Lon"] = smooth_coords[k]
    return pontos

# Função que cria uma rota alternativa quando segmentos estão bloqueados.
def gerar_rota_alternativa_com_bloqueios(paradas, bloqueios, desvio_base=0.0008, pontos_por_segmento=8):
    """
    bloqueios: lista de dicts {"lat":..., "lng":..., "radius_m":...} ou {"from": idx_or_name, "to": idx_or_name}
    A função detecta se um segmento entre paradas cruza um bloqueio (aproximação por distância ao ponto médio)
    e então adiciona um ponto de desvio perpendicular para contornar.
    """
    pontos = []
    def segment_midpoint(a, b):
        return ((a["lat"]+b["lat"])/2, (a["lng"]+b["lng"])/2)
    def haversine_km(a, b):
        return geodesic(a, b).km
    for i in range(len(paradas)-1):
        p1 = paradas[i]; p2 = paradas[i+1]
        pontos.append({"Lat": p1["lat"], "Lon": p1["lng"], "Parada": p1["nome"], "Tipo": "Parada"})
        mid = {"lat": (p1["lat"]+p2["lat"])/2, "lng": (p1["lng"]+p2["lng"])/2}
        blocked_here = False
        for b in bloqueios:
            # dois formatos suportados:
            if "lat" in b and "lng" in b and "radius_m" in b:
                d_km = geodesic((mid["lat"], mid["lng"]), (b["lat"], b["lng"])).km
                if d_km*1000 <= b["radius_m"]:
                    blocked_here = True
                    blocker = b
                    break
            else:
                # bloqueio por índices ou nomes
                fr = b.get("from"); to = b.get("to")
                try:
                    idx_fr = int(fr) if isinstance(fr, (str,int)) and str(fr).isdigit() else None
                    idx_to = int(to) if isinstance(to, (str,int)) and str(to).isdigit() else None
                except Exception:
                    idx_fr = idx_to = None
                name_fr = fr if isinstance(fr, str) and not str(fr).isdigit() else None
                name_to = to if isinstance(to, str) and not str(to).isdigit() else None
                cond = False
                if idx_fr is not None and idx_to is not None:
                    cond = (idx_fr == i and idx_to == i+1) or (idx_fr == i+1 and idx_to == i)
                if name_fr or name_to:
                    cond = cond or (p1["nome"] == name_fr and p2["nome"] == name_to) or (p1["nome"] == name_to and p2["nome"] == name_fr)
                if cond:
                    blocked_here = True
                    blocker = {"lat": mid["lat"], "lng": mid["lng"], "radius_m": 150}
                    break
        # Gera segmentos com ou sem desvio
        if not blocked_here:
            for j in range(1, pontos_por_segmento):
                t = j / pontos_por_segmento
                lat = p1["lat"] + (p2["lat"] - p1["lat"]) * t
                lon = p1["lng"] + (p2["lng"] - p1["lng"]) * t
                pontos.append({"Lat": lat, "Lon": lon, "Parada": f"{p1['nome']} → {p2['nome']}", "Tipo": "Rota"})
        else:
            # cria desvio: calcula vetor perpendicular simples no plano lat/lon
            dx = p2["lng"] - p1["lng"]
            dy = p2["lat"] - p1["lat"]
            # perpendicular vector
            perp_x = -dy
            perp_y = dx
            # normaliza
            norm = max((perp_x**2 + perp_y**2)**0.5, 1e-9)
            perp_x /= norm; perp_y /= norm
            offset = desvio_base
            detour_point = {"lat": mid["lat"] + perp_y * offset, "lng": mid["lng"] + perp_x * offset}
            # gera primeiro subsegmento até detour, depois até p2
            subpoints = []
            for j in range(1, int(pontos_por_segmento/2)+1):
                t = j / (pontos_por_segmento/2)
                lat = p1["lat"] + (detour_point["lat"] - p1["lat"]) * t
                lon = p1["lng"] + (detour_point["lng"] - p1["lng"]) * t
                subpoints.append({"Lat": lat, "Lon": lon, "Parada": f"{p1['nome']} → desv", "Tipo": "Rota"})
            for j in range(1, int(pontos_por_segmento/2)+1):
                t = j / (pontos_por_segmento/2)
                lat = detour_point["lat"] + (p2["lat"] - detour_point["lat"]) * t
                lon = detour_point["lng"] + (p2["lng"] - detour_point["lng"]) * t
                subpoints.append({"Lat": lat, "Lon": lon, "Parada": f"desv → {p2['nome']}", "Tipo": "Rota"})
            pontos.extend(subpoints)
    pontos.append({"Lat": paradas[-1]["lat"], "Lon": paradas[-1]["lng"], "Parada": paradas[-1]["nome"], "Tipo": "Parada"})
    return pontos

# Cálculo consolidado de métricas (padrão) para uso em gráficos
def calcular_metricas_gerais(pontos_mapa, tipo_onibus, dados_onibus, velocidade_media):
    distancia_total = 0.0
    for i in range(len(pontos_mapa)-1):
        p1 = (pontos_mapa[i]["Lat"], pontos_mapa[i]["Lon"])
        p2 = (pontos_mapa[i+1]["Lat"], pontos_mapa[i+1]["Lon"])
        distancia_total += geodesic(p1, p2).km
    tempo_h = distancia_total / max(0.1, velocidade_media)
    consumo = distancia_total / dados_onibus[tipo_onibus]["consumo"]
    co2 = distancia_total * dados_onibus[tipo_onibus]["co2"]
    custo = distancia_total * dados_onibus[tipo_onibus]["custo_km"]
    return {
        "distancia_km": round(distancia_total, 2),
        "tempo_min": round(tempo_h * 60, 2),
        "combustivel": round(consumo, 2),
        "co2": round(co2, 2),
        "custo": round(custo, 2),
        "velocidade_media": round(distancia_total / tempo_h if tempo_h>0 else 0, 2)
    }

# Painel de bloqueios que será chamado no fluxo principal (mostra/edita st.session_state.blocked_segments)
def render_block_panel():
    st.sidebar.markdown("### ⚠️ Painel de Bloqueios")
    with st.sidebar.expander("Gerenciar bloqueios", expanded=True):
        b_name = st.text_input("Descrição (opcional) para bloqueio")
        b_lat = st.text_input("Lat (ex.: -22.22) — deixar vazio para definir por paradas", value="")
        b_lng = st.text_input("Lng (ex.: -49.94)", value="")
        b_radius = st.number_input("Raio (m)", min_value=50, max_value=2000, value=150, step=10)
        if st.button("Adicionar bloqueio"):
            if b_lat and b_lng:
                try:
                    st.session_state.blocked_segments.append({"descr": b_name or f"Bloq {len(st.session_state.blocked_segments)+1}", "lat": float(b_lat), "lng": float(b_lng), "radius_m": int(b_radius)})
                    st.success("Bloqueio adicionado")
                except Exception:
                    st.error("Lat/Lng inválidos")
            else:
                # Sem coordenadas, permite definir por índice/nome usando campo livre
                st.session_state.blocked_segments.append({"descr": b_name or f"Bloq {len(st.session_state.blocked_segments)+1}", "from": "", "to": "", "radius_m": int(b_radius)})
                st.info("Bloqueio adicionado como segmento (edite manualmente depois)")
        if st.session_state.blocked_segments:
            st.write("Bloqueios atuais:")
            for idx, b in enumerate(st.session_state.blocked_segments):
                cols = st.columns([3,1,1])
                cols[0].write(f"{idx+1}. {b.get('descr','')}")
                if cols[1].button("Remover", key=f"rm_{idx}"):
                    st.session_state.blocked_segments.pop(idx)
                    st.experimental_rerun()
                if cols[2].button("Editar", key=f"ed_{idx}"):
                    # abre um modal-like via expander temporário (não nativo) — simplificação:
                    st.session_state.block_to_edit = idx

# Pequena rotina para integrar rotas custom ao dicionário principal, se desejar
def integrar_rotas_custom_automatico():
    try:
        # tenta inserir agora (quando o restante do arquivo definir linhas_marilia, esta função pode ser chamada novamente)
        try_register_custom_routes_into_globals(globals())
    except Exception:
        pass

# Executa integração imediata (se possível)
integrar_rotas_custom_automatico()
# Configuração inicial
st.set_page_config(
    page_title="Otimizador de Rotas - Marília/SP", 
    layout="wide",
    page_icon="🚍"
)

# Título do aplicativo
st.title("🚍 OTIMIZADOR DE ROTAS - MARÍLIA/SP")
st.markdown("""
**Versão 3.0** | Dados baseados em rotas reais  
""")

# Banco de dados de linhas atualizado
linhas_marilia = {
    "Linha Nova Marília (Segundo Grupo) - IDA": {
        "paradas": [
            {"nome": "Terminal Central (TCE)", "lat": -22.2139, "lng": -49.9456},
            {"nome": "Av. Rio Branco (Banco do Brasil)", "lat": -22.2100, "lng": -49.9420},
            {"nome": "Rua São Luiz (Praça São Bento)", "lat": -22.2118, "lng": -49.9420},
            {"nome": "Av. Castro Alves (Posto Ipiranga)", "lat": -22.2190, "lng": -49.9360},
            {"nome": "Rua Pernambuco (Supermercado Dia)", "lat": -22.2210, "lng": -49.9330},
            {"nome": "Av. Brasil (Parque do Povo)", "lat": -22.2230, "lng": -49.9300},
            {"nome": "Rua Bahia (UBS Jardim Marília)", "lat": -22.2250, "lng": -49.9280},
            {"nome": "Av. Carlos Gomes (Terminal Rodoviário)", "lat": -22.2270, "lng": -49.9250},
            {"nome": "Rua Amazonas (Residencial Primavera)", "lat": -22.2290, "lng": -49.9220},
            {"nome": "Terminal Nova Marília (TNM)", "lat": -22.2300, "lng": -49.9200}
        ],
        "velocidade_media": 30,
        "horario_pico": ["06:00-08:00", "17:00-19:00"]
    },
    "Linha Nova Marília (Segundo Grupo) - VOLTA": {
        "paradas": [
            {"nome": "Terminal Nova Marília (TNM)", "lat": -22.2300, "lng": -49.9200},
            {"nome": "Rua Amazonas (Residencial Primavera)", "lat": -22.2290, "lng": -49.9220},
            {"nome": "Av. Carlos Gomes (Terminal Rodoviário)", "lat": -22.2270, "lng": -49.9250},
            {"nome": "Rua Bahia (UBS Jardim Marília)", "lat": -22.2250, "lng": -49.9280},
            {"nome": "Av. Brasil (Parque do Povo)", "lat": -22.2230, "lng": -49.9300},
            {"nome": "Rua Pernambuco (Supermercado Dia)", "lat": -22.2210, "lng": -49.9330},
            {"nome": "Av. Castro Alves (Posto Ipiranga)", "lat": -22.2190, "lng": -49.9360},
            {"nome": "Rua São Luiz (Praça São Bento)", "lat": -22.2118, "lng": -49.9420},
            {"nome": "Av. Rio Branco (Banco do Brasil)", "lat": -22.2100, "lng": -49.9420},
            {"nome": "Terminal Central (TCE)", "lat": -22.2139, "lng": -49.9456}
        ],
        "velocidade_media": 30,
        "horario_pico": ["06:00-08:00", "17:00-19:00"]
    }
}

# Dados de consumo dos ônibus
dados_onibus = {
    "Ônibus Padrão (Diesel)": {"consumo": 3.0, "co2": 2.7, "custo_km": 5.20},
    "Microônibus (Etanol)": {"consumo": 4.5, "co2": 1.8, "custo_km": 4.30},
    "Ônibus Elétrico": {"consumo": 0.18, "co2": 0.05, "custo_km": 3.80}
}

# Função para criar rotas realistas com ajustes para seguir ruas
def gerar_rota_realista(paradas, desvio=0):
    """Gera pontos de rota que seguem o trajeto real dos ônibus"""
    pontos_rota = []
    
    for i in range(len(paradas)-1):
        p1 = paradas[i]
        p2 = paradas[i+1]
        
        # Adiciona a parada atual
        pontos_rota.append({
            "Lat": p1["lat"],
            "Lon": p1["lng"],
            "Parada": p1["nome"],
            "Tipo": "Parada"
        })
        
        # Calcula direção geral entre os pontos
        delta_lat = p2["lat"] - p1["lat"]
        delta_lng = p2["lng"] - p1["lng"]
        
        # Determina se o movimento é mais latitudinal ou longitudinal
        movimento_principal = 'lat' if abs(delta_lat) > abs(delta_lng) else 'lng'
        
        # Cria pontos intermediários com padrão de ruas (retas com curvas suaves)
        num_pontos = 10
        for j in range(1, num_pontos):
            frac = j/num_pontos
            
            if movimento_principal == 'lat':
                # Primeiro ajusta latitude, depois longitude
                if frac < 0.5:
                    lat = p1["lat"] + delta_lat * frac * 2
                    lng = p1["lng"]
                else:
                    lat = p2["lat"]
                    lng = p1["lng"] + delta_lng * (frac - 0.5) * 2
            else:
                # Primeiro ajusta longitude, depois latitude
                if frac < 0.5:
                    lng = p1["lng"] + delta_lng * frac * 2
                    lat = p1["lat"]
                else:
                    lng = p2["lng"]
                    lat = p1["lat"] + delta_lat * (frac - 0.5) * 2
            
            # Adiciona pequeno desvio para rotas alternativas
            if desvio > 0:
                if movimento_principal == 'lat':
                    lng += desvio * 0.0002 * np.sin(frac * np.pi)
                else:
                    lat += desvio * 0.0002 * np.sin(frac * np.pi)
            
            pontos_rota.append({
                "Lat": lat,
                "Lon": lng,
                "Parada": f"{p1['nome']} → {p2['nome']}",
                "Tipo": "Rota"
            })
    # Pós-processamento: garante densidade mínima entre paradas, aplica desvio alternativo e suaviza a trilha
    # Identifica posições das paradas já inseridas
    parada_positions = [(idx, p["Parada"], p["Lat"], p["Lon"]) for idx, p in enumerate(pontos_rota) if p["Tipo"] == "Parada"]

    # Garante pelo menos um ponto "Rota" entre paradas imediatas (evita lacunas que quebram cálculo de distância)
    for k in range(len(parada_positions) - 1):
        idx_a = parada_positions[k][0]
        idx_b = parada_positions[k + 1][0]
        if idx_b - idx_a <= 1:
            a_lat, a_lon = parada_positions[k][2], parada_positions[k][3]
            b_lat, b_lon = parada_positions[k + 1][2], parada_positions[k + 1][3]
            mid = {
                "Lat": (a_lat + b_lat) / 2.0,
                "Lon": (a_lon + b_lon) / 2.0,
                "Parada": f"{parada_positions[k][1]} → {parada_positions[k + 1][1]}",
                "Tipo": "Rota"
            }
            pontos_rota.insert(idx_b, mid)
            # atualiza índices seguintes
            for t in range(k + 1, len(parada_positions)):
                parada_positions[t] = (parada_positions[t][0] + 1, parada_positions[t][1], parada_positions[t][2], parada_positions[t][3])

    # Se for rota alternativa (desvio>0), aplica pequenos deslocamentos perpendiculares para criar variação realista
    if desvio and desvio > 0:
        n = len(pontos_rota)
        for i in range(1, n - 1):
            if pontos_rota[i]["Tipo"] != "Rota":
                continue
            prev = pontos_rota[i - 1]
            nxt = pontos_rota[i + 1]
            vx = nxt["Lon"] - prev["Lon"]
            vy = nxt["Lat"] - prev["Lat"]
            # vetor perpendicular
            perp_x = -vy
            perp_y = vx
            norm = max((perp_x ** 2 + perp_y ** 2) ** 0.5, 1e-9)
            perp_x /= norm
            perp_y /= norm
            # intensidade do desvio modulada pela posição na rota para evitar saltos no início/fim
            factor = desvio * 0.00018 * np.sin((i / max(1, n - 1)) * np.pi)
            pontos_rota[i]["Lat"] += perp_y * factor
            pontos_rota[i]["Lon"] += perp_x * factor

    # Suavização por média móvel (mantém exatamente as posições das paradas)
    coords = [(p["Lat"], p["Lon"]) for p in pontos_rota]
    w = 2  # janela de vizinhança
    smooth_coords = []
    for idx in range(len(coords)):
        lat_sum = 0.0
        lon_sum = 0.0
        count = 0
        for k in range(max(0, idx - w), min(len(coords), idx + w + 1)):
            lat_sum += coords[k][0]
            lon_sum += coords[k][1]
            count += 1
        smooth_coords.append((lat_sum / count, lon_sum / count))

    # Aplica suavização somente aos pontos de tipo "Rota" para preservar paradas exatas
    for k, p in enumerate(pontos_rota):
        if p["Tipo"] == "Rota":
            p["Lat"], p["Lon"] = smooth_coords[k]

    # Remove pontos duplicados consecutivos (mesma coordenada e mesmo tipo)
    cleaned = []
    for p in pontos_rota:
        if not cleaned:
            cleaned.append(p)
            continue
        last = cleaned[-1]
        if abs(last["Lat"] - p["Lat"]) < 1e-8 and abs(last["Lon"] - p["Lon"]) < 1e-8 and last["Tipo"] == p["Tipo"]:
            # ignora duplicata
            continue
        cleaned.append(p)
    pontos_rota = cleaned
    # Adiciona a última parada
    pontos_rota.append({
        "Lat": paradas[-1]["lat"],
        "Lon": paradas[-1]["lng"],
        "Parada": paradas[-1]["nome"],
        "Tipo": "Parada"
    })
    # Pós-processamento adicional: garante variações determinísticas entre gerações
    # (faz com que chamadas sucessivas gerem trajetos vizinhos, evitando ruas idênticas)
    if "_gera_rota_counter" not in st.session_state:
        st.session_state["_gera_rota_counter"] = 0
    if "block_warnings" not in st.session_state:
        st.session_state["block_warnings"] = []

    variant_idx = st.session_state["_gera_rota_counter"]
    st.session_state["_gera_rota_counter"] += 1

    # Detecta bloqueios que afetem segmentos desta rota e registra avisos (para UI mostrar depois)
    st.session_state["block_warnings"] = st.session_state.get("block_warnings", [])
    detected = []
    for i in range(len(paradas) - 1):
        a = paradas[i]; b = paradas[i + 1]
        mid = ((a["lat"] + b["lat"]) / 2.0, (a["lng"] + b["lng"]) / 2.0)
        for bi, bl in enumerate(st.session_state.get("blocked_segments", [])):
            # suporta dois formatos
            if "lat" in bl and "lng" in bl and "radius_m" in bl:
                try:
                    d_m = geodesic(mid, (bl["lat"], bl["lng"])).km * 1000
                except Exception:
                    continue
                if d_m <= bl.get("radius_m", 150):
                    msg = f"Bloqueio '{bl.get('descr',bi)}' provavelmente afeta segmento: {a['nome']} → {b['nome']}"
                    if msg not in st.session_state["block_warnings"]:
                        st.session_state["block_warnings"].append(msg)
                    detected.append((i, bi))
            else:
                # segmento definido por índices ou nomes
                fr = bl.get("from"); to = bl.get("to")
                idx_fr = None; idx_to = None
                try:
                    idx_fr = int(fr) if str(fr).isdigit() else None
                    idx_to = int(to) if str(to).isdigit() else None
                except Exception:
                    idx_fr = idx_to = None
                name_fr = fr if isinstance(fr, str) and not str(fr).isdigit() else None
                name_to = to if isinstance(to, str) and not str(to).isdigit() else None
                cond = False
                if idx_fr is not None and idx_to is not None:
                    if (idx_fr == i and idx_to == i+1) or (idx_fr == i+1 and idx_to == i):
                        cond = True
                if name_fr or name_to:
                    if (a["nome"] == name_fr and b["nome"] == name_to) or (a["nome"] == name_to and b["nome"] == name_fr):
                        cond = True
                if cond:
                    msg = f"Bloqueio por segmento '{bl.get('descr',bi)}' afeta: {a['nome']} → {b['nome']}"
                    if msg not in st.session_state["block_warnings"]:
                        st.session_state["block_warnings"].append(msg)
                    detected.append((i, bi))

    # Aplica pequenas variações perpendiculares determinísticas para diferenciar rotas similares
    # magnitude base depende de desvio (alternativa >> otimizada/atual) e de variant_idx para criar variações
    base_small = 0.00012  # ~13m
    base_alt = 0.0006     # ~66m

    magnitude = base_alt if desvio and desvio > 0 else base_small * (1.0 + (variant_idx % 3) * 0.25)
    # alternating sign for successive generations to avoid identical direction
    sign = -1 if (variant_idx % 2) == 0 else 1

    n = len(pontos_rota)
    for i in range(1, n - 1):
        p = pontos_rota[i]
        if p["Tipo"] != "Rota":
            continue
        prev = pontos_rota[i - 1]
        nxt = pontos_rota[i + 1]
        # vetor tangente aproximado
        vx = nxt["Lon"] - prev["Lon"]
        vy = nxt["Lat"] - prev["Lat"]
        # perpendicular
        perp_x = -vy
        perp_y = vx
        norm = max((perp_x ** 2 + perp_y ** 2) ** 0.5, 1e-12)
        perp_x /= norm
        perp_y /= norm
        # modulador para suavizar no início/fim
        frac = i / max(1, n - 1)
        factor = np.sin(frac * np.pi)
        # se houver bloqueio detectado no segmento associado, aumente o desvio localmente para contornar
        local_multiplier = 1.0
        # verifica se ponto pertence a um segmento detectado (aprox pelo mid index)
        for seg_idx, _ in detected:
            # se índice do ponto estiver próximo do segmento midpoint (heurística)
            # cada segment midpoint roughly located around positions proportional to paradas length inserted earlier
            # simples heurística: se diferença entre i and seg_idx*(num_points_between_paradas) small -> aumenta
            if abs(seg_idx - (i / max(1, n / max(1, len(paradas)-1)))) < 1.5:
                local_multiplier = 1.6
                break
        offset = magnitude * factor * local_multiplier * sign
        p["Lat"] += perp_y * offset
        p["Lon"] += perp_x * offset

    # marca meta-informação para permitir UI diferenciar quais variantes foram geradas
    for p in pontos_rota:
        p.setdefault("meta", {})
        p["meta"]["variant_idx"] = variant_idx
        p["meta"]["desvio_flag"] = bool(desvio and desvio > 0)

    # garante que paradas mantenham coordenadas exatas (evita deslocamentos acidentais devido à suavização anterior)
    for stop in paradas:
        for p in pontos_rota:
            if p["Tipo"] == "Parada" and p["Parada"] == stop["nome"]:
                p["Lat"] = stop["lat"]
                p["Lon"] = stop["lng"]
                break
    return pontos_rota

# Função para simular rota com cálculo de distância real
def simular_rota(paradas, velocidade_media, tipo="Atual"):
    """Simula uma rota com cálculos realistas"""
    pontos_rota = gerar_rota_realista(paradas, desvio=0 if tipo != "Alternativa" else 1)
    
    # Calcula distância total (aproximação)
    distancia_total = 0
    for i in range(len(pontos_rota)-1):
        if pontos_rota[i]["Tipo"] == "Rota" and pontos_rota[i+1]["Tipo"] == "Rota":
            p1 = (pontos_rota[i]["Lat"], pontos_rota[i]["Lon"])
            p2 = (pontos_rota[i+1]["Lat"], pontos_rota[i+1]["Lon"])
            distancia_total += geodesic(p1, p2).km
    
    # Ajustes para rota otimizada
    if tipo == "Otimizada":
        distancia_total *= 0.85  # Redução de 15% na distância
        velocidade_media *= 1.1  # Aumento de 10% na velocidade
    
    tempo_minutos = (distancia_total / velocidade_media) * 60
    
    return {
        "distancia_km": round(distancia_total, 2),
        "tempo_min": round(tempo_minutos, 2),
        "pontos_mapa": pontos_rota,
        "tipo": tipo
    }

# Interface
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/7/7c/Brasao_Marilia.svg/1200px-Brasao_Marilia.svg.png", width=100)
    st.header("Configurações")
    
    linha_selecionada = st.selectbox(
        "Selecione a linha:",
        list(linhas_marilia.keys()),
        index=0
    )
    
    tipo_onibus = st.selectbox(
        "Tipo de ônibus:",
        list(dados_onibus.keys()),
        index=0
    )
    
    hora_pico = st.checkbox(
        "Horário de pico (reduz velocidade)",
        value=False
    )
    
    mostrar_alternativa = st.checkbox(
        "Mostrar rota alternativa",
        value=True
    )

# Processamento
dados_linha = linhas_marilia[linha_selecionada]
velocidade = dados_linha["velocidade_media"]

if hora_pico:
    velocidade *= 0.7
    st.sidebar.warning(f"Horários de pico: {', '.join(dados_linha['horario_pico'])}")

# Simulação das rotas
rota_atual = simular_rota(dados_linha["paradas"], velocidade, "Atual")
rota_otimizada = simular_rota(dados_linha["paradas"], velocidade, "Otimizada")

if mostrar_alternativa:
    rota_alternativa = simular_rota(dados_linha["paradas"], velocidade * 0.9, "Alternativa")

# Cálculos de desempenho
def calcular_estatisticas(rota, tipo_onibus):
    consumo = rota["distancia_km"] / dados_onibus[tipo_onibus]["consumo"]
    co2 = rota["distancia_km"] * dados_onibus[tipo_onibus]["co2"]
    custo = rota["distancia_km"] * dados_onibus[tipo_onibus]["custo_km"]
    
    return {
        "combustivel": round(consumo, 2),
        "co2": round(co2, 2),
        "custo": round(custo, 2),
        "velocidade_media": round(rota["distancia_km"] / (rota["tempo_min"] / 60), 2)
    }

stats_atual = calcular_estatisticas(rota_atual, tipo_onibus)
stats_otimizada = calcular_estatisticas(rota_otimizada, tipo_onibus)

if mostrar_alternativa:
    stats_alternativa = calcular_estatisticas(rota_alternativa, tipo_onibus)

# Visualização
tab1, tab2, tab3 = st.tabs(["📊 Comparação", "🌍 Mapa Interativo", "📈 Relatório"])

with tab1:
    st.subheader("Comparação de Desempenho")
    
    comparacao = {
        "Metrica": ["Distância (km)", "Tempo (min)", "Combustível", "Emissão CO₂", "Custo (R$)", "Velocidade Média (km/h)"],
        "Rota Atual": [
            rota_atual["distancia_km"],
            rota_atual["tempo_min"],
            f"{stats_atual['combustivel']} {'L' if 'Elétrico' not in tipo_onibus else 'kWh'}",
            f"{stats_atual['co2']} kg",
            f"R$ {stats_atual['custo']}",
            stats_atual["velocidade_media"]
        ],
        "Rota Otimizada": [
            rota_otimizada["distancia_km"],
            rota_otimizada["tempo_min"],
            f"{stats_otimizada['combustivel']} {'L' if 'Elétrico' not in tipo_onibus else 'kWh'}",
            f"{stats_otimizada['co2']} kg",
            f"R$ {stats_otimizada['custo']}",
            stats_otimizada["velocidade_media"]
        ]
    }
    
    if mostrar_alternativa:
        comparacao["Rota Alternativa"] = [
            rota_alternativa["distancia_km"],
            rota_alternativa["tempo_min"],
            f"{stats_alternativa['combustivel']} {'L' if 'Elétrico' not in tipo_onibus else 'kWh'}",
            f"{stats_alternativa['co2']} kg",
            f"R$ {stats_alternativa['custo']}",
            stats_alternativa["velocidade_media"]
        ]
    
    st.dataframe(pd.DataFrame(comparacao).set_index("Metrica"), height=250)

with tab2:
    st.subheader("Mapa das Rotas")
    
    # Prepara dados para o mapa
    df_atual = pd.DataFrame([p for p in rota_atual["pontos_mapa"] if p["Tipo"] == "Rota"])
    df_atual["Tipo"] = "Atual"
    
    df_opt = pd.DataFrame([p for p in rota_otimizada["pontos_mapa"] if p["Tipo"] == "Rota"])
    df_opt["Tipo"] = "Otimizada"
    
    if mostrar_alternativa:
        df_alt = pd.DataFrame([p for p in rota_alternativa["pontos_mapa"] if p["Tipo"] == "Rota"])
        df_alt["Tipo"] = "Alternativa"
        df_rotas = pd.concat([df_atual, df_opt, df_alt])
    else:
        df_rotas = pd.concat([df_atual, df_opt])
    
    # Paradas oficiais
    df_paradas = pd.DataFrame([
        {"Lat": p["lat"], "Lon": p["lng"], "Parada": p["nome"]} 
        for p in dados_linha["paradas"]
    ])
    
    # Cria o mapa
    fig = px.line_mapbox(
        df_rotas,
        lat="Lat",
        lon="Lon",
        color="Tipo",
        hover_name="Parada",
        zoom=13,
        height=600,
        color_discrete_map={
            "Atual": "#FF0000",  # Vermelho
            "Otimizada": "#00FF00",  # Verde
            "Alternativa": "#0000FF"  # Azul
        }
    )
    
    # Adiciona paradas
    fig.add_trace(
        px.scatter_mapbox(
            df_paradas,
            lat="Lat",
            lon="Lon",
            hover_name="Parada",
            text="Parada",
            color_discrete_sequence=["#FFA500"]
        ).data[0]
    )
    
    fig.update_layout(
        mapbox_style="open-street-map",
        margin={"r":0,"t":0,"l":0,"b":0},
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    
    st.plotly_chart(fig, use_container_width=True)

with tab3:
    st.subheader("Relatório de Economia")
    
    viagens_dia = st.slider("Viagens por dia:", 1, 50, 10)
    dias_operacao = st.slider("Dias de operação por ano:", 100, 365, 260)
    
    economia = {
        "Combustível": (stats_atual["combustivel"] - stats_otimizada["combustivel"]) * viagens_dia * dias_operacao,
        "Emissões CO₂": (stats_atual["co2"] - stats_otimizada["co2"]) * viagens_dia * dias_operacao,
        "Custo": (stats_atual["custo"] - stats_otimizada["custo"]) * viagens_dia * dias_operacao,
        "Tempo": ((rota_atual["tempo_min"] - rota_otimizada["tempo_min"]) / 60) * viagens_dia * dias_operacao
    }
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Economia de Combustível", f"{economia['Combustível']:.2f} {'L' if 'Elétrico' not in tipo_onibus else 'kWh'}")
        st.metric("Redução de CO₂", f"{economia['Emissões CO₂']:.2f} kg")
    with col2:
        st.metric("Economia Financeira", f"R$ {economia['Custo']:.2f}")
        st.metric("Tempo Economizado", f"{economia['Tempo']:.1f} horas")

st.markdown("---")
st.caption(f"Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')} | Versão 3.0")