# total_torneo.py
import streamlit as st
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import unicodedata
import json
import pathlib

try:
    import regex as re  # type: ignore
    HAS_REGEX = True
except Exception:
    import re  # type: ignore
    HAS_REGEX = False


HEADER_RE = re.compile(r'^\[([^\]]+)\]\s*(.*)$')
NUMBER_RE = re.compile(r'\d[\d,.]*\d|\d')

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


# ----------------------------
# Helpers (texto / emojis) — mismos que en la app de rondas
# ----------------------------
def split_graphemes(s: str) -> list[str]:
    s = unicodedata.normalize("NFC", s).replace(" ", "")
    if not s:
        return []
    if HAS_REGEX:
        return [g for g in re.findall(r"\X", s) if g and g != "\u200d"]
    return list(s)


def format_points(n: int) -> str:
    return f"{int(n):,}"


def mexico_city_now_str() -> str:
    """Fecha/hora actual en UTC-6 (Ciudad de México, sin horario de verano)."""
    now = datetime.now(timezone.utc) - timedelta(hours=6)
    return f"{now.day} de {MESES_ES[now.month]} de {now.year}, {now.strftime('%H:%M')} (hora CDMX)"


def render_total_output(kind: str, totals: dict) -> str:
    """
    kind: "parcial" o "final".
    El resultado se puede volver a pegar como input en una corrida futura
    (split_messages lo reconoce como un bloque suelto).
    """
    ranking = sorted(TEAM_ORDER, key=lambda emo: (-int(totals.get(emo, 0)), emo))

    if kind == "final":
        header = "🏁 Total final de la copa"
    else:
        header = f"📊 Total parcial hasta aquí - {mexico_city_now_str()}"

    lines = [header] + [f" {emo} {format_points(int(totals.get(emo, 0)))}" for emo in ranking]
    return "\n".join(lines)


# ----------------------------
# Carga de equipos (mismo teams.json que la app de rondas)
# ----------------------------
_TEAMS_FILE = pathlib.Path(__file__).parent / "teams.json"
_DEFAULT_CONFIG = {
    "teams": [
        {"emoji": "🧡", "name": "Equipo Naranja"},
        {"emoji": "💜", "name": "Equipo Morado"},
        {"emoji": "🩵", "name": "Equipo Celeste"},
        {"emoji": "🩷", "name": "Equipo Rosa"},
    ]
}


def load_teams() -> tuple[list[str], dict[str, str]]:
    if not _TEAMS_FILE.exists():
        _TEAMS_FILE.write_text(
            json.dumps(_DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    config = json.loads(_TEAMS_FILE.read_text(encoding="utf-8"))
    teams = config.get("teams", config) if isinstance(config, dict) else config
    order = [t["emoji"] for t in teams]
    names = {t["emoji"]: t["name"] for t in teams}
    return order, names


TEAM_ORDER, TEAM_NAME = load_teams()
TEAM_SET = set(TEAM_ORDER)


# ----------------------------
# Parsing de mensajes de WhatsApp
# ----------------------------
def split_messages(raw_text: str):
    """
    Cada mensaje de WhatsApp empieza con "[hora, fecha] remitente: ...".
    Todo lo que sigue (hasta el próximo mensaje) pertenece a ese mensaje.

    También soporta bloques "sueltos" SIN encabezado de WhatsApp — por ejemplo,
    cuando se vuelve a pegar un "Total parcial" generado por esta misma app en
    una corrida anterior. En ese caso, la primera línea del bloque se trata
    como si fuera el título/encabezado (mismo trato que la primera línea de un
    mensaje normal).

    Se normaliza NFKC para convertir dígitos/decoraciones "fancy" (𝟱𝟴𝟬, etc.)
    a su forma estándar antes de procesar cualquier cosa.
    """
    text = unicodedata.normalize("NFKC", raw_text)
    lines = text.splitlines()

    messages = []
    current = None

    for line in lines:
        line = line.rstrip("\n\r")
        if not line.strip():
            continue

        m = HEADER_RE.match(line)
        if m:
            # "header" guarda la línea completa (con fecha/hora) solo para mostrarla
            # en advertencias/debug. Para escanear equipos+números usamos SOLO el
            # contenido del mensaje (m.group(2)), así la fecha ("4/9" o "5/9/2026",
            # en cualquier orden con la hora) no se confunde con un puntaje sin equipo.
            current = {"header": line, "lines": [m.group(2)]}
            messages.append(current)
        elif current is None:
            # Bloque suelto sin encabezado "[...]" — ej. un Total Parcial re-pegado.
            # Esta línea se vuelve el "título" de ese bloque.
            current = {"header": line, "lines": [line]}
            messages.append(current)
        else:
            current["lines"].append(line)

    return messages


def parse_line(line: str):
    """
    Devuelve (numeros_encontrados, equipos_distintos_encontrados) para una línea.
    Un mismo equipo puede aparecer repetido en la línea (ej. "🧡🧡  7.000  🧡🧡"
    en la versión de WhatsApp para celular) sin que eso cuente como ambigüedad.
    """
    nums = NUMBER_RE.findall(line)
    graphemes = split_graphemes(line)
    teams_found = [g for g in graphemes if g in TEAM_SET]
    distinct_teams = list(dict.fromkeys(teams_found))  # preserva orden, sin duplicados
    return nums, distinct_teams


def compute_totals(messages: list[dict]):
    totals: dict[str, int] = defaultdict(int)
    warnings: list[dict] = []
    breakdown: list[dict] = []  # por mensaje, para el debug expander

    for msg in messages:
        msg_totals: dict[str, int] = {}
        for line_idx, line in enumerate(msg["lines"]):
            if not line.strip():
                continue

            nums, teams_found = parse_line(line)
            is_header_line = (line_idx == 0)

            if not nums and not teams_found:
                continue  # línea irrelevante (título, texto normal, etc.)

            if nums and len(teams_found) == 1:
                number = int(nums[0].replace(",", "").replace(".", ""))
                team = teams_found[0]
                totals[team] += number
                msg_totals[team] = msg_totals.get(team, 0) + number
                if len(nums) > 1:
                    warnings.append({
                        "header": msg["header"],
                        "line": line,
                        "reason": f"Se encontró más de un número en la línea, se usó el primero ({nums[0]}).",
                    })

            elif nums and not teams_found:
                # La primera línea suele ser el título de la dinámica (ej. "Express 31
                # y 01", "Reto Miércoles 2") y puede traer números que no son puntajes.
                # Solo se advierte si esto pasa en líneas posteriores (donde sí se
                # esperan puntajes).
                if not is_header_line:
                    warnings.append({
                        "header": msg["header"],
                        "line": line,
                        "reason": "Se encontró un número pero ningún emoji de equipo reconocido en la línea.",
                    })

            elif nums and len(teams_found) > 1:
                warnings.append({
                    "header": msg["header"],
                    "line": line,
                    "reason": f"Línea ambigua: se encontraron {len(teams_found)} emojis de equipo distintos.",
                })

            elif teams_found and not nums:
                warnings.append({
                    "header": msg["header"],
                    "line": line,
                    "reason": "Se encontró un emoji de equipo pero no pude extraer un número.",
                })

        if msg_totals:
            breakdown.append({"header": msg["header"], "totals": msg_totals})

    return totals, warnings, breakdown


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="Total del torneo Warriors", layout="centered")
st.title("📈 Total del torneo Warriors")

with st.expander("ℹ️ Cómo funciona"):
    st.markdown(
        """
Pega aquí el export completo de WhatsApp (o solo los mensajes que quieras sumar).
La app detecta cada mensaje por su encabezado `[hora, fecha] remitente: ...`
y busca, dentro de cada uno, líneas con un emoji de equipo + un número.

También puedes pegar directamente un **Total parcial** generado antes por
esta misma app (sin encabezado de WhatsApp) — se reconoce igual y sus puntos
se suman como si fuera un mensaje más. Así no hace falta re-pegar todo el
historial cada vez: basta con guardar el último total parcial y pegarlo junto
con los mensajes nuevos.

Soporta números "decorados" con dígitos estilizados (como `𝟱,𝟴𝟱𝟬`) y
símbolos alrededor (`꒷`, `𖦹`, `˙`, etc.) — se normalizan automáticamente.

Si una línea no se puede interpretar con confianza (número sin equipo,
equipo sin número, o algo ambiguo), no se descarta en silencio: aparece
en la lista de advertencias con el mensaje y la línea exacta para que la
revises tú mismo.
"""
    )

tipo_total = st.radio(
    "Tipo de resultado a generar",
    options=["Total parcial", "Total final"],
    horizontal=True,
)

text = st.text_area("Pega aquí los mensajes de WhatsApp (o un Total parcial anterior + mensajes nuevos)", height=520)

if st.button("Calcular total del torneo"):
    messages = split_messages(text)

    if not messages:
        st.error("No se detectó ningún contenido para procesar. Revisa que hayas pegado el texto.")
    else:
        totals, warnings, breakdown = compute_totals(messages)

        st.write(f"Mensajes/bloques detectados: **{len(messages)}**")
        st.write(f"Con puntajes reconocidos: **{len(breakdown)}**")

        if warnings:
            st.warning(f"{len(warnings)} línea(s) necesitan revisión:")
            for w in warnings:
                st.markdown(f"- **Mensaje:** `{w['header'][:80]}`")
                st.markdown(f"  **Línea:** `{w['line']}`")
                st.markdown(f"  **Motivo:** {w['reason']}")

        kind = "final" if tipo_total == "Total final" else "parcial"
        st.markdown(f"### {'🏁' if kind == 'final' else '📊'} {tipo_total} (para copiar)")
        st.code(render_total_output(kind, totals), language="text")

        with st.expander("Ver tabla de puntos (interno)"):
            st.table([
                {"Equipo": emo, "Nombre": TEAM_NAME.get(emo, ""), "Puntos": format_points(int(totals.get(emo, 0)))}
                for emo in TEAM_ORDER
            ])

        with st.expander("Ver desglose por mensaje (debug)"):
            for item in breakdown:
                st.markdown(f"**{item['header'][:100]}**")
                for emo, pts in sorted(item["totals"].items(), key=lambda x: (-x[1], x[0])):
                    st.write(f"  {emo}: {format_points(pts)}")
                st.markdown("---")
