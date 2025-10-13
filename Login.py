import os
import streamlit as st
from auth import login_page, logout_and_rerun
import re

BASE_PROJECTS_DIR = "projects"
os.makedirs(BASE_PROJECTS_DIR, exist_ok=True)

# --- Force login ---
if "logged_in" not in st.session_state or not st.session_state.get("logged_in"):
    login_page()   
    st.stop()      
def safe_name(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z.-]", "", (s or "guest"))
    s = re.sub(r"[^0-9a-zA-Z.-]", "", s).strip()
    

USERNAME = safe_name(st.session_state.get("username", "guest"))
first_name = st.session_state.get("first_name", "Guest")
last_name = st.session_state.get("last_name", "")
full_name = f"{first_name} {last_name}".strip()
PROJECTS_DIR = os.path.join(BASE_PROJECTS_DIR, USERNAME)
os.makedirs(PROJECTS_DIR, exist_ok=True)


# contoh fungsi safe_name dan project_path (pastikan tidak membuat double-nesting)
import re
def safe_name(name: str, max_len: int = 120) -> str:
    s = re.sub(r"[^0-9a-zA-Z_\-\.]", "_", (name or "untitled"))
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:max_len] or "project").lower()

def project_path(proj_name: str) -> str:
    p = os.path.join(PROJECTS_DIR, safe_name(proj_name))
    os.makedirs(p, exist_ok=True)
    return p

def project_db_path(proj_name: str) -> str:
    return os.path.join(project_path(proj_name), "project.db")

# di sidebar: tombol logout
with st.sidebar:
    st.write(f"👋 Logged in as: **{full_name}**")
    if st.button("🚪 Logout"):
        logout_and_rerun()
