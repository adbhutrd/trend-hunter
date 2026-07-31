"""Night Purple theme — dark, vibrant, consistent. Simple is best."""

PROFESSIONAL_CSS = """
<style>
    /* ═══════════════════════════════════════════════════════════════
       GLOBAL — deep purple-black canvas
       ═══════════════════════════════════════════════════════════════ */
    .stApp, .main > div {
        background: #0f0a1a !important;
    }
    .block-container {
        max-width: 1400px !important;
        padding: 1rem 1.5rem !important;
        margin: 0 auto;
    }

    /* ═══════════════════════════════════════════════════════════════
       SIDEBAR — deep dark purple
       ═══════════════════════════════════════════════════════════════ */
    section[data-testid="stSidebar"] {
        background: #150d24 !important;
        border-right: 1px solid #2a1f3b !important;
        width: 240px !important;
        min-width: 240px !important;
    }
    section[data-testid="stSidebar"] > div:first-child {
        padding: 1.2rem 0.8rem !important;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] .st-emotion-cache-1wrcr25 {
        color: #e2e8f0 !important;
        font-weight: 700 !important;
    }
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p {
        color: #94a3b8 !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
    }

    /* Sidebar navigation */
    div[data-testid="stSidebarNav"] ul {
        list-style: none !important;
        padding: 0.25rem 0 !important;
        margin: 0 !important;
    }
    div[data-testid="stSidebarNav"] li {
        margin: 0.1rem 0 !important;
    }
    div[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"] {
        display: flex !important;
        align-items: center !important;
        gap: 0.5rem !important;
        padding: 0.45rem 0.75rem !important;
        border-radius: 8px !important;
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        text-decoration: none !important;
        transition: all 0.12s ease !important;
        border: none !important;
        background: transparent !important;
        color: #94a3b8 !important;
    }
    div[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"] * {
        color: inherit !important;
        font-size: inherit !important;
        font-weight: inherit !important;
    }
    div[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"]:hover {
        background: #1f1531 !important;
        color: #c4b5fd !important;
    }
    div[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"]:hover * {
        color: inherit !important;
    }
    div[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"][aria-current="page"] {
        background: #2a1f3b !important;
        color: #c4b5fd !important;
        font-weight: 600 !important;
    }
    div[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"][aria-current="page"] * {
        color: inherit !important;
        font-weight: inherit !important;
    }

    section[data-testid="stSidebar"] hr {
        border-color: #2a1f3b !important;
        margin: 0.8rem 0 !important;
    }
    section[data-testid="stSidebar"] .stButton button {
        background: #1f1531;
        color: #e2e8f0;
        border: 1px solid #2a1f3b;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.82rem;
        padding: 0.4rem 0.8rem;
        transition: all 0.12s ease;
    }
    section[data-testid="stSidebar"] .stButton button:hover {
        background: #2a1f3b;
        border-color: #7c3aed;
    }
    section[data-testid="stSidebar"] .stMetric label {
        color: #94a3b8 !important;
        font-size: 0.7rem !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    section[data-testid="stSidebar"] .stMetric div {
        color: #e2e8f0 !important;
        font-size: 1.2rem !important;
        font-weight: 700 !important;
    }
    section[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] {
        background: #1f1531;
        border-color: #2a1f3b;
        border-radius: 8px;
    }
    section[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"]:hover {
        border-color: #7c3aed;
    }
    section[data-testid="stSidebar"] .stSlider label {
        color: #94a3b8 !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       METRIC CARDS — clean dark cards with purple accent
       ═══════════════════════════════════════════════════════════════ */
    div[data-testid="metric-container"] {
        background: #150d24;
        border: 1px solid #2a1f3b;
        border-radius: 10px;
        padding: 1rem 1.25rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        transition: all 0.15s ease;
    }
    div[data-testid="metric-container"]:hover {
        box-shadow: 0 4px 16px rgba(124,58,237,0.15);
        border-color: #7c3aed;
    }
    div[data-testid="metric-container"] > label {
        font-size: 0.7rem !important;
        color: #64748b !important;
        font-weight: 700 !important;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 0.2rem !important;
    }
    div[data-testid="metric-container"] > div {
        font-size: 1.75rem !important;
        font-weight: 800 !important;
        color: #e2e8f0 !important;
        line-height: 1.2 !important;
    }
    div[data-testid="metric-container"] div[data-testid="stMetricDelta"] {
        font-size: 0.78rem !important;
        font-weight: 600 !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       TABLES — dark themed
       ═══════════════════════════════════════════════════════════════ */
    .stDataFrame {
        border: 1px solid #2a1f3b;
        border-radius: 10px;
        overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        background: #150d24;
    }
    .stDataFrame thead tr th {
        background: #1a0f2a !important;
        font-weight: 700 !important;
        font-size: 0.72rem !important;
        color: #94a3b8 !important;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        padding: 0.75rem 1rem !important;
        border-bottom: 2px solid #2a1f3b !important;
    }
    .stDataFrame tbody tr td {
        padding: 0.65rem 1rem !important;
        border-bottom: 1px solid #1f1531 !important;
        font-size: 0.85rem !important;
        color: #cbd5e1 !important;
        background: #150d24 !important;
    }
    .stDataFrame tbody tr:hover td {
        background: #1f1531 !important;
    }
    .stDataFrame tbody tr:last-child td {
        border-bottom: none !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       HEADERS
       ═══════════════════════════════════════════════════════════════ */
    h1 {
        color: #e2e8f0 !important;
        font-weight: 800 !important;
        font-size: 1.6rem !important;
        letter-spacing: -0.02em;
    }
    h2 {
        color: #e2e8f0 !important;
        font-weight: 700 !important;
        font-size: 1.25rem !important;
        letter-spacing: -0.01em;
        margin-top: 1.5rem !important;
        border-left: 3px solid #7c3aed;
        padding-left: 12px;
    }
    h3 {
        color: #cbd5e1 !important;
        font-weight: 700 !important;
        font-size: 1.05rem !important;
    }
    h4, h5, h6 {
        color: #e2e8f0 !important;
    }
    p, li, span, div {
        color: #cbd5e1;
    }
    .stCaption {
        color: #64748b !important;
        font-size: 0.82rem !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       ALERT BOXES — dark variant
       ═══════════════════════════════════════════════════════════════ */
    .stAlert {
        border-radius: 10px !important;
        border: none !important;
    }
    div[data-testid="stInfoBox"] {
        background: #1a1030 !important;
        border-left: 4px solid #7c3aed !important;
        border-radius: 10px !important;
        color: #c4b5fd !important;
    }
    div[data-testid="stSuccessBox"] {
        background: #0f1f10 !important;
        border-left: 4px solid #22c55e !important;
        border-radius: 10px !important;
        color: #86efac !important;
    }
    div[data-testid="stWarningBox"] {
        background: #1f1a0f !important;
        border-left: 4px solid #f59e0b !important;
        border-radius: 10px !important;
        color: #fde68a !important;
    }
    div[data-testid="stErrorBox"] {
        background: #1f0f0f !important;
        border-left: 4px solid #ef4444 !important;
        border-radius: 10px !important;
        color: #fca5a5 !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       BUTTONS — purple primary
       ═══════════════════════════════════════════════════════════════ */
    .stButton button {
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
        transition: all 0.12s ease;
        border: none !important;
        padding: 0.35rem 1rem !important;
    }
    .stButton button[kind="primary"],
    .stButton button:not([kind="secondary"]) {
        background: #7c3aed !important;
        color: white !important;
    }
    .stButton button[kind="primary"]:hover,
    .stButton button:not([kind="secondary"]):hover {
        background: #6d28d9 !important;
        box-shadow: 0 2px 12px rgba(124,58,237,0.35);
    }
    .stButton button[kind="secondary"] {
        background: #1f1531 !important;
        color: #94a3b8 !important;
        border: 1px solid #2a1f3b !important;
    }
    .stButton button[kind="secondary"]:hover {
        background: #2a1f3b !important;
        border-color: #7c3aed !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       TEXT INPUTS
       ═══════════════════════════════════════════════════════════════ */
    .stTextInput input {
        border-radius: 8px !important;
        border: 1.5px solid #2a1f3b !important;
        padding: 0.5rem 0.75rem !important;
        font-size: 0.88rem !important;
        color: #e2e8f0 !important;
        background: #150d24 !important;
    }
    .stTextInput input:focus {
        border-color: #7c3aed !important;
        box-shadow: 0 0 0 3px rgba(124,58,237,0.15) !important;
    }
    .stTextInput input::placeholder {
        color: #475569 !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       SELECT BOXES
       ═══════════════════════════════════════════════════════════════ */
    .stSelectbox div[data-baseweb="select"] {
        border-radius: 8px !important;
        border: 1.5px solid #2a1f3b !important;
        background: #150d24 !important;
    }
    .stSelectbox div[data-baseweb="select"]:focus-within {
        border-color: #7c3aed !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       EXPANDERS
       ═══════════════════════════════════════════════════════════════ */
    div[data-testid="stExpander"] {
        border: 1px solid #2a1f3b !important;
        border-radius: 10px !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        overflow: hidden;
        background: #150d24;
    }
    div[data-testid="stExpander"]:hover {
        border-color: #7c3aed !important;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 600;
        color: #e2e8f0;
        padding: 0.75rem 1rem !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       DIVIDERS
       ═══════════════════════════════════════════════════════════════ */
    hr, div[data-testid="stDivider"] {
        margin: 1.5rem 0 !important;
        border: none !important;
        border-top: 1px solid #2a1f3b !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       TABS
       ═══════════════════════════════════════════════════════════════ */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        border-bottom: 2px solid #2a1f3b;
        background: transparent;
    }
    .stTabs button {
        font-weight: 600;
        font-size: 0.85rem;
        padding: 0.65rem 1.25rem;
        border: none !important;
        color: #64748b !important;
        transition: color 0.12s ease;
        background: transparent !important;
    }
    .stTabs button[aria-selected="true"] {
        color: #c4b5fd !important;
        border-bottom: 2px solid #7c3aed !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       NAVIGATION CARDS (Home page)
       ═══════════════════════════════════════════════════════════════ */
    .nav-card {
        background: #150d24;
        border: 1px solid #2a1f3b;
        border-radius: 10px;
        padding: 1.25rem;
        transition: all 0.15s ease;
        cursor: default;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    }
    .nav-card:hover {
        border-color: #7c3aed;
        box-shadow: 0 4px 16px rgba(124,58,237,0.15);
        transform: translateY(-1px);
    }

    /* ═══════════════════════════════════════════════════════════════
       CHARTS
       ═══════════════════════════════════════════════════════════════ */
    .stChart, div[data-testid="stChart"] {
        background: #150d24;
        border-radius: 10px;
        padding: 0.5rem;
        border: 1px solid #2a1f3b;
    }

    /* ═══════════════════════════════════════════════════════════════
       SLIDER
       ═══════════════════════════════════════════════════════════════ */
    .stSlider div[data-baseweb="slider"] > div:first-child {
        background: #7c3aed !important;
    }
    .stSlider div[data-baseweb="slider"] div[role="slider"] {
        background: #c4b5fd !important;
        border: 2px solid #7c3aed !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       MULTI-SELECT
       ═══════════════════════════════════════════════════════════════ */
    div[data-baseweb="select"] > div {
        border-radius: 8px !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       SPINNER
       ═══════════════════════════════════════════════════════════════ */
    .stSpinner > div {
        border-top-color: #7c3aed !important;
    }
</style>
"""


# ── Price band categories used across all dashboard pages ─────────────────
PRICE_BANDS = [
    ("🟢 Budget (£1–£100)", 1, 100),
    ("🔵 Mid-Range (£100–£300)", 100, 300),
    ("🟠 Premium (£300–£600)", 300, 600),
    ("🔴 Luxury (£600–£1,000)", 600, 1_000),
]


def apply_professional_theme():
    """Inject night purple CSS into the Streamlit page."""
    import streamlit as st
    st.markdown(PROFESSIONAL_CSS, unsafe_allow_html=True)
