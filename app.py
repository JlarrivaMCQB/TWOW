# TWOWTE Streamlit App – Versión 2025‑05‑19
# =============================================================================
# Cambios principales sobre la última versión:
#   • Todos los usuarios empiezan con 0 monedas (incl. admin).
#   • Los jueces NO pueden enviar frases; la pestaña Acción sólo muestra voto‑info.
#   • Panel Admin recupera las tres opciones: **Añadir**, Desactivar y Rehabilitar.
# =============================================================================
import streamlit as st
import sqlite3
import numpy as np
import random
import datetime as dt

DB = "game.db"
conn = sqlite3.connect(DB, check_same_thread=False)
c = conn.cursor()

# ---------- 1. Esquema de tablas --------------------------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS users(
  username TEXT PRIMARY KEY,
  password TEXT NOT NULL,
  role TEXT NOT NULL,            -- 'jugador' | 'juez'
  is_admin INTEGER NOT NULL,
  coins INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1)
""")

c.execute("CREATE TABLE IF NOT EXISTS settings(clave TEXT PRIMARY KEY, valor TEXT)")

c.execute("""
CREATE TABLE IF NOT EXISTS rounds(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  numero INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS frases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  texto TEXT NOT NULL,
  autor TEXT NOT NULL,
  round_id INTEGER NOT NULL)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS votos(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  juez TEXT NOT NULL,
  frase_id INTEGER NOT NULL,
  posicion INTEGER NOT NULL)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS player_round(
  round_id INTEGER NOT NULL,
  username TEXT NOT NULL,
  responses_left INTEGER NOT NULL,
  df_flag INTEGER NOT NULL DEFAULT 0,
  multiplier INTEGER NOT NULL DEFAULT 1,
  penalty INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(round_id,username))
""")

c.execute("""
CREATE TABLE IF NOT EXISTS purchases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id INTEGER NOT NULL,
  username TEXT NOT NULL,
  item TEXT NOT NULL,
  meta TEXT)
""")
conn.commit()
# aseguramos que la columna penalty exista si la tabla venía de versiones previas
try:
    c.execute("ALTER TABLE player_round ADD COLUMN penalty INTEGER NOT NULL DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

# ---------- 2. Seed: solo admin con 0 monedas -------------------------------
if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
    c.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", ("Jlarriva", "FioreIsQueen", "juez", 1, 0, 1))
    conn.commit()

# ---------- 3. Ajustes por defecto ------------------------------------------
DEFAULTS = {
  "titulo": "TWOWTE – Reality de Frases",
  "current_round": "1",
  "reward_first": "10",
  "reward_second": "7",
  "reward_third": "5",
  "reward_45": "3",
  "reward_participate": "1"
}
for k, v in DEFAULTS.items():
    c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
conn.commit()
get_setting = lambda k: c.execute("SELECT valor FROM settings WHERE clave=?", (k,)).fetchone()[0]
set_setting = lambda k, v: (c.execute("REPLACE INTO settings VALUES(?,?)", (k, str(v))), conn.commit())

# ---------- 4. Garantizar ronda abierta -------------------------------------
current_round = int(get_setting("current_round"))
open_r = c.execute("SELECT id FROM rounds WHERE numero=? AND status='open'", (current_round,)).fetchone()
if not open_r:
    c.execute("INSERT INTO rounds(numero,status,created_at) VALUES(?,?,?)", (current_round, 'open', dt.datetime.utcnow().isoformat()))
    rid = c.lastrowid
    players = c.execute("SELECT username FROM users WHERE active=1").fetchall()
    c.executemany("INSERT INTO player_round(round_id,username,responses_left) VALUES(?,?,1)", [(rid, p[0]) for p in players])
    conn.commit()
    open_r = (rid,)
round_id = open_r[0]

# ---------- 5. Utilidades ----------------------------------------------------
# Función para cerrar ronda automáticamente cuando todos los jueces han votado

def auto_close_round():
    global current_round, round_id
    frases = c.execute("SELECT id, texto, autor FROM frases WHERE round_id=?", (round_id,)).fetchall()
    if not frases:
        return
    N = len(frases)
    pts = {fid: 0 for fid, _, _ in frases}
    pos_map = {fid: [] for fid, _, _ in frases}
    for fid, pos in c.execute(
        "SELECT frase_id, posicion FROM votos WHERE frase_id IN (SELECT id FROM frases WHERE round_id=? )",
        (round_id,)):
        pts[fid] += N + 1 - pos
        pos_map[fid].append(pos)

    results = []
    for fid, txt, aut in frases:
        std = float(np.std(pos_map[fid])) if pos_map[fid] else 0.0
        pen = c.execute("SELECT penalty FROM player_round WHERE round_id=? AND username=?", (round_id, aut)).fetchone()[0]
        total = pts[fid] + pen
        df  = c.execute("SELECT df_flag FROM player_round WHERE round_id=? AND username=?", (round_id, aut)).fetchone()[0]
        results.append({"Autor": aut, "Puntos": total, "DF": bool(df), "STD": std, "Frase": txt})
    results.sort(key=lambda r: (r["Puntos"], r["DF"], r["STD"]), reverse=True)

    st.subheader(f"Resultados ronda {current_round}")
    st.table(results)

    # premios
    recomp = [int(get_setting("reward_first")), int(get_setting("reward_second")), int(get_setting("reward_third")), int(get_setting("reward_45")), int(get_setting("reward_45"))]
    orden = [r["Autor"] for r in results]
    for idx, pl in enumerate(orden):
        reward = recomp[idx] if idx < len(recomp) else int(get_setting("reward_participate"))
        mult = c.execute("SELECT multiplier FROM player_round WHERE round_id=? AND username=?", (round_id, pl)).fetchone()[0]
        c.execute("UPDATE users SET coins = coins + ? WHERE username=?", (reward * mult, pl))

    # Determinar eliminado: peor "mejor" puesto
    best_pos = {}
    for idx, auth in enumerate(orden):
        if auth not in best_pos:
            best_pos[auth] = idx  # primera aparición (mejor puesto)
    eliminado = sorted(best_pos.items(), key=lambda x: x[1])[-1][0]
    c.execute("UPDATE users SET active=0 WHERE username=?", (eliminado,))
    c.execute("UPDATE rounds SET status='closed' WHERE id=?", (round_id,))

    # preparar nueva ronda
    next_num = current_round + 1
    set_setting("current_round", next_num)
    c.execute("INSERT INTO rounds(numero,status,created_at) VALUES(?,?,?)", (next_num, 'open', dt.datetime.utcnow().isoformat()))
    new_rid = c.lastrowid
    activos = c.execute("SELECT username FROM users WHERE active=1").fetchall()
    c.executemany("INSERT INTO player_round(round_id, username, responses_left) VALUES(?,?,1)", [(new_rid, a[0]) for a in activos])
    conn.commit()

    round_id = new_rid
    current_round = next_num
    st.success(f"Ronda {next_num -1} cerrada automáticamente. Eliminado: {eliminado}. ¡Nueva ronda disponible!")


def load_users(active_only=False):
    q = "SELECT username,password,role,is_admin,coins,active FROM users" + (" WHERE active=1" if active_only else "")
    return {u[0]: u for u in c.execute(q).fetchall()}
users = load_users()

def total_judges():
    return sum(1 for u in users.values() if u[2] == 'juez' and u[5] == 1)

# ---------- 6. Streamlit & sesión -------------------------------------------
st.set_page_config(page_title="TWOWTE", page_icon="📝", layout="centered")
if 'user' not in st.session_state:
    st.session_state['user'] = None
    st.session_state['is_admin'] = False

st.title(get_setting("titulo"))

# --- Login
if not st.session_state['user']:
    st.sidebar.header("Login")
    u = st.sidebar.text_input("Usuario")
    p = st.sidebar.text_input("Contraseña", type="password")
    if st.sidebar.button("Entrar"):
        if u in users and users[u][1] == p and users[u][5] == 1:
            st.session_state['user'] = u
            st.session_state['is_admin'] = bool(users[u][3])
            st.rerun()
        else:
            st.sidebar.error("Credenciales incorrectas o cuenta inactiva")
    st.stop()

# ---------- 7. Tabs ----------------------------------------------------------
base_tabs = ["Acción", "Tienda", "Resultados", "Historial"]
if st.session_state['is_admin']:
    base_tabs.append("Admin")
tabs = st.tabs(base_tabs)

username = st.session_state['user']
is_admin = st.session_state['is_admin']

###############################################################################
# ACCIÓN                                                                      #
###############################################################################
with tabs[0]:
    # Botón para refrescar sin cerrar sesión
    if st.button("🔄 Recargar valores"):
        st.rerun()

    role = users[username][2]
    if role == 'juez':
        st.info("Eres juez: no envías frases, solo votas.")
        # Interfaz de votación para jueces
        frases_j = c.execute("SELECT id, texto FROM frases WHERE round_id=?", (round_id,)).fetchall()
        if not frases_j:
            st.warning("Aún no hay frases para votar.")
        else:
            labels = [txt for _, txt in frases_j]
            id_map = {txt: fid for fid, txt in frases_j}
            ranking = st.multiselect("Ordena de mejor a peor", labels, default=[], key="rank")
            if len(ranking) == len(labels):
                if st.button("Enviar voto"):
                    c.execute("DELETE FROM votos WHERE juez=? AND frase_id IN (SELECT id FROM frases WHERE round_id=? )", (username, round_id))
                    for pos, label in enumerate(ranking, 1):
                        fid = id_map[label]
                        c.execute("INSERT INTO votos(juez, frase_id, posicion) VALUES(?,?,?)", (username, fid, pos))
                    conn.commit(); st.success("Voto registrado")
            else:
                st.info("Selecciona todas las frases para completar el ranking.")
    else:
        # Formulario de envío para jugadores
        pr_state = c.execute("SELECT responses_left FROM player_round WHERE round_id=? AND username=?", (round_id, username)).fetchone()
        if not pr_state:
            st.error("No participas en esta ronda.")
        else:
            left = pr_state[0]
            st.info(f"Respuestas restantes: {left}")
            if left > 0:
                frase_txt = st.text_input("Tu frase:")
                if st.button("Enviar frase") and frase_txt.strip():
                    c.execute("INSERT INTO frases(texto, autor, round_id) VALUES(?,?,?)", (frase_txt.strip(), username, round_id))
                    c.execute("UPDATE player_round SET responses_left = responses_left - 1 WHERE round_id=? AND username=?", (round_id, username))
                    conn.commit(); st.success("Frase enviada"); st.rerun()
            enviados = set(x[0] for x in c.execute("SELECT DISTINCT autor FROM frases WHERE round_id=?", (round_id,)))
            if len(enviados) >= 2:
                faltan = [u for u in users if users[u][5] == 1 and users[u][2] == 'jugador' and u not in enviados]
                random.shuffle(faltan)
                st.write("Pendientes:", ", ".join(faltan) if faltan else "Todos han enviado")

# TIENDA                                                                      #
###############################################################################
SHOP = {"Doble Respuesta": 10, "Triple Respuesta": 25, "Desempate Favorable": 8, "Ruleta del Tigre": 9, "Duplicador de Monedas": 12}
with tabs[1]:
    # Manejo de Ruleta pendiente
    if st.session_state.get("pending_ruleta", False) and st.session_state.get("ruleta_buyer") == username:
        st.subheader("Configurar Ruleta del Tigre")
        r1 = st.text_input("Jugador 1")
        r2 = st.text_input("Jugador 2")
        if st.button("Ejecutar Ruleta"):
            users = load_users()
            valid = all(r in users and users[r][5] == 1 for r in [r1, r2]) and r1 != r2 and r1 not in ["", username] and r2 not in ["", username]
            if valid:
                price = SHOP["Ruleta del Tigre"]
                coins_user = c.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0]
                if coins_user < price:
                    st.error("Monedas insuficientes")
                else:
                    loser = random.choice([username, r1, r2])
                    c.execute("UPDATE users SET coins = coins - 3 WHERE username=?", (loser,))
                    c.execute("UPDATE users SET coins = coins - ? WHERE username=?", (price, username))
                    c.execute("INSERT INTO purchases(round_id, username, item, meta) VALUES(?,?,?,?)", (round_id, username, "Ruleta del Tigre", f"{r1}|{r2}"))
                    conn.commit()
                    st.success(f"Perdedor: {loser}")
                    # reset flags
                    st.session_state["pending_ruleta"] = False
                    st.session_state["ruleta_buyer"] = None
                    st.rerun()
            else:
                st.error("Jugadores inválidos o repetidos")
        st.stop()

    row_coins = c.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()
    coins = row_coins[0] if row_coins else 0
    st.write(f"Monedas: **{coins}**")
    bought = c.execute("SELECT item FROM purchases WHERE round_id=? AND username=?", (round_id, username)).fetchone()
    if bought:
        st.info(f"Ya compraste {bought[0]} esta ronda.")
    else:
        for itm, price in SHOP.items():
            colA, colB = st.columns([3, 1])
            colA.write(f"**{itm}** – {price} monedas")
            if colB.button(f"Comprar {itm}"):
                if coins < price:
                    st.error("Monedas insuficientes")
                else:
                    # efectos inmediatos
                    if itm == "Doble Respuesta":
                        c.execute("UPDATE player_round SET responses_left = responses_left + 1 WHERE round_id=? AND username=?", (round_id, username))
                    elif itm == "Triple Respuesta":
                        c.execute("UPDATE player_round SET responses_left = responses_left + 2 WHERE round_id=? AND username=?", (round_id, username))
                    elif itm == "Desempate Favorable":
                        c.execute("UPDATE player_round SET df_flag = 1 WHERE round_id=? AND username=?", (round_id, username))
                    elif itm == "Duplicador de Monedas":
                        c.execute("UPDATE player_round SET multiplier = 2 WHERE round_id=? AND username=?", (round_id, username))
                    elif itm == "Ruleta del Tigre":
                        # guardar estado en sesión y pedir nombres en nuevo render
                        st.session_state["pending_ruleta"] = True
                        st.session_state["ruleta_buyer"] = username
                        st.rerun()
                    # Cobrar y registrar compra (genérico para otros ítems)
                    c.execute("UPDATE users SET coins = coins - ? WHERE username=?", (price, username))
                    c.execute("INSERT INTO purchases(round_id, username, item) VALUES(?,?,?)", (round_id, username, itm))
                    conn.commit(); st.success("Compra aplicada"); st.rerun()

###############################################################################
# RESULTADOS                                                                  #
###############################################################################
with tabs[2]:
    enviados = c.execute("SELECT COUNT(DISTINCT autor) FROM frases WHERE round_id=?", (round_id,)).fetchone()[0]
    if enviados == 0:
        st.info("Aún no hay frases enviadas.")
    else:
        need = total_judges()
        got = c.execute(
            "SELECT COUNT(DISTINCT juez) FROM votos WHERE frase_id IN (SELECT id FROM frases WHERE round_id=? )",
            (round_id,)).fetchone()[0]
        if got < need:
            st.info(f"Faltan votos de {need - got} juez(es).")
        else:
            # Cierre automático si la ronda sigue abierta
            if c.execute("SELECT status FROM rounds WHERE id=?", (round_id,)).fetchone()[0] == 'open':
                auto_close_round()

            # ---- Mostrar resultados finales ----
            frases = c.execute("SELECT id, texto, autor FROM frases WHERE round_id=?", (round_id,)).fetchall()
            N = len(frases)
            pts = {fid: 0 for fid, _, _ in frases}
            pos_list = {fid: [] for fid, _, _ in frases}
            for fid, pos in c.execute(
                "SELECT frase_id, posicion FROM votos WHERE frase_id IN (SELECT id FROM frases WHERE round_id=? )",
                (round_id,)):
                pts[fid] += N + 1 - pos
                pos_list[fid].append(pos)
            results = []
            for fid, txt, aut in frases:
                std = float(np.std(pos_list[fid])) if pos_list[fid] else 0.0
                df = c.execute("SELECT df_flag FROM player_round WHERE round_id=? AND username=?", (round_id, aut)).fetchone()[0]
                pen = c.execute("SELECT penalty FROM player_round WHERE round_id=? AND username=?", (round_id, aut)).fetchone()[0]
                total_pts = pts[fid] + pen  # sumar penalizaciones negativas o positivas
                results.append({"Autor": aut, "Puntos": total_pts, "DF": bool(df), "STD": std, "Frase": txt})
            results.sort(key=lambda r: (r["Puntos"], r["DF"], r["STD"]), reverse=True)
            st.table(results)

with tabs[3]:
    # --- Historial de rondas ---
    closed = c.execute("SELECT id, numero FROM rounds WHERE status='closed' ORDER BY numero").fetchall()
    wins = {u: 0 for u in users}
    avgs = {u: [] for u in users}
    for rid, num in closed:
        fr = c.execute("SELECT id, autor FROM frases WHERE round_id=?", (rid,)).fetchall()
        N = len(fr)
        pts = {a: 0 for _, a in fr}
        for fid, pos in c.execute("SELECT frase_id, posicion FROM votos WHERE frase_id IN (SELECT id FROM frases WHERE round_id=? )", (rid,)):
            aut = c.execute("SELECT autor FROM frases WHERE id=?", (fid,)).fetchone()[0]
            pts[aut] += N + 1 - pos
        if pts:
            ranking = sorted(pts, key=pts.get, reverse=True)
            wins[ranking[0]] += 1
            for rk, p in enumerate(ranking, 1):
                avgs[p].append(rk)
    # Estadísticas de jugadores (sin jueces)
    stats = [{
        "Jugador": u,
        "Victorias": wins[u],
        "Promedio": round(np.mean(avgs[u]), 2) if avgs[u] else "-"
    } for u in users if users[u][2] == 'jugador']
    st.table(stats)

###############################################################################
# ADMIN                                                                       #
###############################################################################
if is_admin:
    with tabs[-1]:
        st.header("Panel Admin")
        # --- Notificación de Ruleta del Tigre comprada esta ronda ---
        ruletas = c.execute("SELECT username, meta FROM purchases WHERE round_id=? AND item='Ruleta del Tigre'", (round_id,)).fetchall()
        if ruletas:
            st.subheader("Ruletas del Tigre compradas")
            for u, meta in ruletas:
                rival1, rival2 = (meta or "|").split("|")
                st.write(f"**{u}** retó a **{rival1}** y **{rival2}**")
        st.markdown("---")
        # Cambiar título principal
        st.subheader("Editar título de la temporada")
        new_title = st.text_input("Nuevo título", get_setting("titulo"))
        if st.button("Actualizar título"):
            set_setting("titulo", new_title.strip() or get_setting("titulo"))
            st.success("Título actualizado – recarga para ver el cambio")
        # Añadir jugador
        st.subheader("Añadir nuevo jugador")
        new_user = st.text_input("Usuario nuevo")
        new_pass = st.text_input("Contraseña nueva")
        new_role = st.selectbox("Rol", ["jugador", "juez"])
        if st.button("Crear jugador"):
            if new_user in users:
                st.error("El usuario ya existe")
            elif not new_user or not new_pass:
                st.error("Usuario y contraseña obligatorios")
            else:
                c.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (new_user, new_pass, new_role, 0, 0, 1))
                # también agregar a ronda actual
                c.execute("INSERT INTO player_round(round_id, username, responses_left) VALUES(?,?,1)", (round_id, new_user))
                conn.commit(); st.success("Jugador añadido"); st.rerun()

        st.markdown("---")
        # Desactivar / habilitar
        colA, colB = st.columns(2)
        with colA:
            des = st.selectbox("Desactivar", [u for u in users if users[u][5] == 1])
            if st.button("Desactivar"):
                c.execute("UPDATE users SET active=0 WHERE username=?", (des,))
                conn.commit(); st.success("Desactivado"); st.rerun()
        with colB:
            reh = st.selectbox("Rehabilitar", [u for u in users if users[u][5] == 0])
            if st.button("Rehabilitar"):
                c.execute("UPDATE users SET active=1 WHERE username=?", (reh,))
                # añadir al player_round si no existe para ronda actual
                if not c.execute("SELECT 1 FROM player_round WHERE round_id=? AND username=?", (round_id, reh)).fetchone():
                    c.execute("INSERT INTO player_round(round_id, username, responses_left) VALUES(?,?,1)", (round_id, reh))
                conn.commit(); st.success("Rehabilitado"); st.rerun()

        st.markdown("---")
        # Recompensas configurables
        col1, col2, col3, col4 = st.columns(4)
        r1 = col1.number_input("1º", value=int(get_setting("reward_first")))
        r2 = col2.number_input("2º", value=int(get_setting("reward_second")))
        r3 = col3.number_input("3º", value=int(get_setting("reward_third")))
        r45 = col4.number_input("4º-5º", value=int(get_setting("reward_45")))
        if st.button("Guardar recompensas"):
            set_setting("reward_first", r1); set_setting("reward_second", r2); set_setting("reward_third", r3); set_setting("reward_45", r45)
            st.success("Recompensas guardadas")

        st.markdown("---")
        # Ajustar monedas, penalización y respuestas
        st.subheader("Ajustar parámetros de jugador")
        sel_user = st.selectbox("Jugador", list(users.keys()))
        delta_coins = st.number_input("± Monedas", value=0, step=1, format="%d")
        delta_pen  = st.number_input("± Penalización de puntos", value=0, step=1, format="%d")
        delta_resp = st.number_input("± Respuestas restantes", value=0, step=1, format="%d")
        if st.button("Aplicar ajustes"):
            if delta_coins:
                c.execute("UPDATE users SET coins = coins + ? WHERE username=?", (delta_coins, sel_user))
            if delta_pen:
                c.execute("UPDATE player_round SET penalty = penalty + ? WHERE round_id=? AND username=?", (delta_pen, round_id, sel_user))
            if delta_resp:
                c.execute("UPDATE player_round SET responses_left = responses_left + ? WHERE round_id=? AND username=?", (delta_resp, round_id, sel_user))
            conn.commit(); st.success("Ajustes aplicados"); st.rerun()

        st.markdown("---")
        # --- Reinicio TOTAL de la base de datos ---
        st.subheader("Reiniciar base de datos")
        confirm = st.checkbox("⚠️ Confirmo reinicio completo (esto borra TODO)")
        if st.button("Ejecutar reinicio"):
            if confirm:
                tables = ["frases", "votos", "rounds", "purchases", "player_round", "users"]
                for tbl in tables:
                    if tbl == "users":
                        c.execute("DELETE FROM users WHERE username <> 'Jlarriva'")
                    else:
                        c.execute(f"DELETE FROM {tbl}")
                set_setting("current_round", 1)
                # crear ronda 1 y reiniciar ajustes temporales
                c.execute("INSERT INTO rounds(numero,status,created_at) VALUES(1,'open',?)", (dt.datetime.utcnow().isoformat(),))
                new_rid = c.lastrowid
                c.execute("INSERT INTO player_round(round_id, username, responses_left) VALUES(?,?,1)", (new_rid, 'Jlarriva'))
                conn.commit()
                st.success("Base reiniciada. Solo la cuenta admin permanece. Recarga la página.")
                st.rerun()
            else:
                st.error("Marca la casilla de confirmación primero.")
        st.markdown("---")
        # Cerrar ronda
        if st.button("Cerrar ronda y otorgar premios"):
            frases = c.execute("SELECT id, autor FROM frases WHERE round_id=?", (round_id,)).fetchall()
            if not frases:
                st.error("Sin frases para esta ronda")
            else:
                N = len(frases)
                pts = {a: 0 for _, a in frases}
                for fid, pos in c.execute("SELECT frase_id, posicion FROM votos WHERE frase_id IN (SELECT id FROM frases WHERE round_id=? )", (round_id,)):
                    aut = c.execute("SELECT autor FROM frases WHERE id=?", (fid,)).fetchone()[0]
                    pts[aut] += N + 1 - pos
                ord_list = sorted(pts, key=pts.get, reverse=True)
                rewards = [
                    int(get_setting("reward_first")),
                    int(get_setting("reward_second")),
                    int(get_setting("reward_third")),
                    int(get_setting("reward_45")),
                    int(get_setting("reward_45"))
                ]
                for idx, pl in enumerate(ord_list):
                    reward = rewards[idx] if idx < len(rewards) else int(get_setting("reward_participate"))
                    mult = c.execute("SELECT multiplier FROM player_round WHERE round_id=? AND username=?", (round_id, pl)).fetchone()[0]
                    c.execute("UPDATE users SET coins = coins + ? WHERE username=?", (reward * mult, pl))
                best_pos = {}
                for idx, au in enumerate(ord_list):
                    if au not in best_pos:
                        best_pos[au] = idx
                eliminado = sorted(best_pos.items(), key=lambda x: x[1])[-1][0]
                c.execute("UPDATE users SET active=0 WHERE username=?", (eliminado,))
                c.execute("UPDATE rounds SET status='closed' WHERE id=?", (round_id,))
                next_num = current_round + 1
                set_setting("current_round", next_num)
                c.execute("INSERT INTO rounds(numero,status,created_at) VALUES(?,?,?)", (next_num, 'open', dt.datetime.utcnow().isoformat()))
                new_rid = c.lastrowid
                activos = c.execute("SELECT username FROM users WHERE active=1").fetchall()
                c.executemany("INSERT INTO player_round(round_id, username, responses_left) VALUES(?,?,1)", [(new_rid, a[0]) for a in activos])
                conn.commit()
                st.success(f"Ronda cerrada. Eliminado: {eliminado}. Ronda {next_num} abierta.")
                st.rerun()
