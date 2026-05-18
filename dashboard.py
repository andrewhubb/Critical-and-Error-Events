"""
Critical & Error Events Monitoring Dashboard
Reads data/events.csv produced by Collect-CriticalEvents.ps1 and provides
an interactive overview of server health across the monitored estate.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Critical & Error Events Monitor",
    page_icon=":rotating_light:",
    layout="wide",
    initial_sidebar_state="expanded",
)

DATA_PATH = Path(__file__).parent / "Data" / "events.csv"

LEVEL_COLOURS = {
    "Critical":         "#d62728",
    "Error":            "#ff7f0e",
    "CONNECTION ERROR": "#7f7f7f",
}

# Sources that are well-known Windows background noise.
# Events from these sources are greyed out in charts rather than highlighted.
# Add or remove entries here to tune what counts as noise in your environment.
NOISE_SOURCES = {
    "Microsoft-Windows-TPM-WMI",        # Secure Boot cert update pending - cosmetic
    "Microsoft-Windows-Perflib",        # Performance counter DLL load failures - very common
    "Microsoft-Windows-DistributedCOM", # DCOM/mobsync background errors
}

NOISE_COLOUR      = "#888888"
ACTIONABLE_COLOUR = "#ff7f0e"

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def load_data() -> pd.DataFrame:
    if not DATA_PATH.exists():
        return pd.DataFrame()

    df = pd.read_csv(DATA_PATH, dtype=str)

    for col in ("CollectedAt", "TimeGenerated"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    if "EventId" in df.columns:
        df["EventId"] = pd.to_numeric(df["EventId"], errors="coerce").astype("Int64")

    if "LevelValue" in df.columns:
        df["LevelValue"] = pd.to_numeric(df["LevelValue"], errors="coerce")

    # Derived columns
    if "TimeGenerated" in df.columns:
        df["Date"] = df["TimeGenerated"].dt.date

    return df


# ---------------------------------------------------------------------------
# Sidebar - filters
# ---------------------------------------------------------------------------
def build_sidebar(df: pd.DataFrame):
    st.sidebar.title("Filters")

    # Date range
    if df.empty or "TimeGenerated" not in df.columns:
        date_min = datetime.today().date() - timedelta(days=7)
        date_max = datetime.today().date()
    else:
        valid = df["TimeGenerated"].dropna()
        date_min = valid.min().date() if not valid.empty else datetime.today().date() - timedelta(days=7)
        date_max = valid.max().date() if not valid.empty else datetime.today().date()

    date_from, date_to = st.sidebar.date_input(
        "Event date range",
        value=(date_min, date_max),
        min_value=date_min,
        max_value=date_max,
    )

    # Servers
    all_servers = sorted(df["Server"].dropna().unique().tolist()) if not df.empty else []
    selected_servers = st.sidebar.multiselect("Servers", all_servers, default=all_servers)

    # Level
    all_levels = sorted(df["Level"].dropna().unique().tolist()) if not df.empty else []
    selected_levels = st.sidebar.multiselect("Level", all_levels, default=all_levels)

    # Log name
    all_logs = sorted(df["LogName"].dropna().unique().tolist()) if not df.empty else []
    selected_logs = st.sidebar.multiselect("Log", all_logs, default=all_logs)

    st.sidebar.markdown("---")
    hide_noise = st.sidebar.toggle(
        "Hide noise events",
        value=False,
        help="Removes known background noise sources (TPM-WMI, Perflib, DistributedCOM) from all views.",
    )

    if st.sidebar.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

    last_updated = DATA_PATH.stat().st_mtime if DATA_PATH.exists() else None
    if last_updated:
        st.sidebar.caption(
            f"CSV last modified: {datetime.fromtimestamp(last_updated).strftime('%Y-%m-%d %H:%M')}"
        )

    return date_from, date_to, selected_servers, selected_levels, selected_logs, hide_noise


def apply_filters(df, date_from, date_to, servers, levels, logs, hide_noise=False):
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if "TimeGenerated" in df.columns:
        mask &= df["TimeGenerated"].dt.date.between(date_from, date_to)
    if servers:
        mask &= df["Server"].isin(servers)
    if levels:
        mask &= df["Level"].isin(levels)
    if logs:
        mask &= df["LogName"].isin(logs)
    if hide_noise and "Source" in df.columns:
        mask &= ~df["Source"].isin(NOISE_SOURCES)
    return df[mask].copy()


# ---------------------------------------------------------------------------
# Metric cards
# ---------------------------------------------------------------------------
def render_metrics(df: pd.DataFrame):
    total      = len(df[df["Level"] != "CONNECTION ERROR"])
    critical   = len(df[df["Level"] == "Critical"])
    error      = len(df[df["Level"] == "Error"])
    conn_err   = len(df[df["Level"] == "CONNECTION ERROR"])
    affected   = df[df["Level"].isin(["Critical", "Error"])]["Server"].nunique()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Events",        total)
    c2.metric("Critical",            critical,  delta=None)
    c3.metric("Error",               error)
    c4.metric("Servers Affected",    affected)
    c5.metric("Connection Errors",   conn_err)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def chart_events_over_time(df: pd.DataFrame):
    if df.empty or "Date" not in df.columns:
        st.info("No event data to chart.")
        return

    plot_df = (
        df[df["Level"].isin(["Critical", "Error"])]
        .groupby(["Date", "Level"])
        .size()
        .reset_index(name="Count")
    )

    if plot_df.empty:
        st.info("No Critical or Error events in selected range.")
        return

    fig = px.bar(
        plot_df,
        x="Date",
        y="Count",
        color="Level",
        color_discrete_map=LEVEL_COLOURS,
        title="Events per Day",
        labels={"Date": "", "Count": "Event Count"},
        barmode="stack",
    )
    fig.update_layout(legend_title_text="", height=350)
    st.plotly_chart(fig, use_container_width=True)


def chart_events_by_server(df: pd.DataFrame):
    if df.empty:
        return

    plot_df = (
        df[df["Level"].isin(["Critical", "Error"])]
        .groupby(["Server", "Level"])
        .size()
        .reset_index(name="Count")
    )

    if plot_df.empty:
        st.info("No Critical or Error events in selected range.")
        return

    fig = px.bar(
        plot_df,
        x="Count",
        y="Server",
        color="Level",
        color_discrete_map=LEVEL_COLOURS,
        title="Events by Server",
        orientation="h",
        barmode="stack",
        labels={"Count": "Event Count", "Server": ""},
    )
    fig.update_layout(legend_title_text="", height=max(300, len(plot_df["Server"].unique()) * 35 + 80))
    fig.update_yaxes(categoryorder="total ascending")
    st.plotly_chart(fig, use_container_width=True)


def chart_top_sources(df: pd.DataFrame, top_n: int = 15):
    if df.empty:
        return

    plot_df = (
        df[df["Level"].isin(["Critical", "Error"])]
        .groupby("Source")
        .size()
        .reset_index(name="Count")
        .nlargest(top_n, "Count")
        .sort_values("Count", ascending=True)
    )

    if plot_df.empty:
        return

    plot_df["Category"] = plot_df["Source"].apply(
        lambda s: "Background noise" if s in NOISE_SOURCES else "Needs attention"
    )

    colour_map = {
        "Needs attention":  ACTIONABLE_COLOUR,
        "Background noise": NOISE_COLOUR,
    }

    fig = px.bar(
        plot_df,
        x="Count",
        y="Source",
        color="Category",
        color_discrete_map=colour_map,
        orientation="h",
        title=f"Top {top_n} Event Sources",
        labels={"Count": "Event Count", "Source": "", "Category": ""},
        category_orders={"Category": ["Needs attention", "Background noise"]},
    )
    fig.update_layout(
        height=max(300, top_n * 28 + 80),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def chart_heatmap(df: pd.DataFrame):
    if df.empty or "Date" not in df.columns:
        return

    plot_df = (
        df[df["Level"].isin(["Critical", "Error"])]
        .groupby(["Server", "Date"])
        .size()
        .reset_index(name="Count")
    )

    if plot_df.empty:
        return

    pivot = plot_df.pivot(index="Server", columns="Date", values="Count").fillna(0)

    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=[str(c) for c in pivot.columns],
            y=pivot.index.tolist(),
            colorscale="Reds",
            hoverongaps=False,
            hovertemplate="Server: %{y}<br>Date: %{x}<br>Events: %{z}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Event Heatmap (Server × Day)",
        xaxis_title="",
        yaxis_title="",
        height=max(300, len(pivot) * 30 + 120),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Server status table
# ---------------------------------------------------------------------------
def render_server_status(df: pd.DataFrame, all_servers: list):
    """Show one row per server with latest collection status.
    df should be date-filtered but NOT noise-filtered so status always reflects reality."""
    if df.empty:
        st.info("No data loaded.")
        return

    rows = []
    for server in sorted(all_servers):
        s_df = df[df["Server"] == server]
        if s_df.empty:
            # Server was never collected in the selected date range - skip entirely
            continue

        conn_err = s_df[s_df["Level"] == "CONNECTION ERROR"]
        if not conn_err.empty:
            rows.append({
                "Server":       server,
                "Status":       "CONNECTION ERROR",
                "Critical":     0,
                "Error":        0,
                "Last Event":   "N/A",
                "Collection Run": str(conn_err["CollectedAt"].max())[:16] if "CollectedAt" in conn_err.columns else "N/A",
            })
            continue

        crit  = len(s_df[s_df["Level"] == "Critical"])
        err   = len(s_df[s_df["Level"] == "Error"])
        last_event = s_df["TimeGenerated"].max()
        last_run   = s_df["CollectedAt"].max() if "CollectedAt" in s_df.columns else None

        status = "OK" if (crit + err) == 0 else ("CRITICAL" if crit > 0 else "ERRORS")

        rows.append({
            "Server":         server,
            "Status":         status,
            "Critical":       crit,
            "Error":          err,
            "Last Event":     str(last_event)[:16] if pd.notna(last_event) else "N/A",
            "Collection Run": str(last_run)[:16]   if last_run is not None and pd.notna(last_run) else "N/A",
        })

    status_df = pd.DataFrame(rows)

    def _colour(val):
        colours = {
            "CRITICAL":         "background-color: #d62728; color: white",
            "ERRORS":           "background-color: #ff7f0e; color: white",
            "CONNECTION ERROR": "background-color: #7f7f7f; color: white",
            "OK":               "background-color: #2ca02c; color: white",
        }
        return colours.get(val, "")

    styled = status_df.style.map(_colour, subset=["Status"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Events table
# ---------------------------------------------------------------------------
def render_events_table(df: pd.DataFrame):
    if df.empty:
        st.info("No events match the current filters.")
        return

    display_cols = [c for c in
        ["TimeGenerated", "Server", "LogName", "EventId", "Level", "Source", "Message", "CollectionMethod"]
        if c in df.columns]

    show_df = df[display_cols].sort_values("TimeGenerated", ascending=False).reset_index(drop=True)

    # Truncate message for display
    if "Message" in show_df.columns:
        show_df["Message"] = show_df["Message"].str[:200]

    st.dataframe(show_df, use_container_width=True, height=500)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download filtered CSV",
        data=csv_bytes,
        file_name=f"events_export_{datetime.today().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    st.title(":rotating_light: Critical & Error Events Monitor")

    df_raw = load_data()

    if df_raw.empty:
        st.warning(
            f"No data found at `{DATA_PATH}`. "
            "Run `Collect-CriticalEvents.ps1` first to generate the CSV."
        )
        st.stop()

    date_from, date_to, sel_servers, sel_levels, sel_logs, hide_noise = build_sidebar(df_raw)
    df = apply_filters(df_raw, date_from, date_to, sel_servers, sel_levels, sel_logs, hide_noise)

    # Server status always uses date-filtered data without noise suppression so
    # servers that only generated noise events still show as OK rather than No data.
    df_status = apply_filters(df_raw, date_from, date_to, sel_servers, sel_levels, sel_logs, hide_noise=False)

    render_metrics(df)
    st.markdown("---")

    tab_overview, tab_table, tab_status = st.tabs(
        ["Overview", "Events Table", "Server Status"]
    )

    with tab_overview:
        col_left, col_right = st.columns(2)
        with col_left:
            chart_events_over_time(df)
            chart_heatmap(df)
        with col_right:
            chart_events_by_server(df)
            chart_top_sources(df)

    with tab_table:
        st.subheader("Events")
        # Inline filters so you can narrow by log and level without touching the sidebar
        fi_col1, fi_col2 = st.columns(2)
        with fi_col1:
            tab_logs = fi_col1.multiselect(
                "Log",
                options=sorted(df["LogName"].dropna().unique().tolist()),
                default=sorted(df["LogName"].dropna().unique().tolist()),
                key="tab_log_filter",
            )
        with fi_col2:
            tab_levels = fi_col2.multiselect(
                "Level",
                options=sorted(df["Level"].dropna().unique().tolist()),
                default=sorted(df["Level"].dropna().unique().tolist()),
                key="tab_level_filter",
            )
        df_table = df.copy()
        if tab_logs:
            df_table = df_table[df_table["LogName"].isin(tab_logs)]
        if tab_levels:
            df_table = df_table[df_table["Level"].isin(tab_levels)]
        render_events_table(df_table)

    with tab_status:
        st.subheader("Server Status")
        all_servers = sorted(df_raw["Server"].dropna().unique().tolist())
        render_server_status(df_status, all_servers)


if __name__ == "__main__":
    main()
