import streamlit as st
import sqlite3, os, hashlib
from datetime import datetime
from pathlib import Path

BASE_PROJECTS_DIR = "projects"
USERS_DB = os.path.join(BASE_PROJECTS_DIR, "users.db")
os.makedirs(BASE_PROJECTS_DIR, exist_ok=True)

def init_users_db():
    """Initialize the users database with first name and last name columns"""
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    pw_hash TEXT,
                    salt TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP)""")
    conn.commit()
    conn.close()

def _hash_password(password: str, salt_hex: str) -> str:
    """Hash password using salt"""
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
    return dk.hex()

def create_user(username: str, password: str, first_name: str, last_name: str):
    """Create user in the database"""
    init_users_db()
    username = username.strip()
    
    if not username or not password or not first_name or not last_name:
        return False, "All fields are required (Username, Password, First Name, Last Name)."

    try:
        conn = sqlite3.connect(USERS_DB)
        c = conn.cursor()
        
        c.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        if c.fetchone():
            return False, "Username already registered."

        salt = os.urandom(16).hex()
        pw_hash = _hash_password(password, salt)

        c.execute("INSERT INTO users (username, pw_hash, salt, first_name, last_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (username, pw_hash, salt, first_name, last_name, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        return True, "Account Created."

    except sqlite3.Error as e:
        return False, f"Database error: {str(e)}"

def verify_user(username: str, password: str) -> bool:
    """Verify if the user credentials are correct"""
    init_users_db()
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute("SELECT pw_hash, salt, first_name, last_name FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False, None, None
    pw_hash_db, salt, first_name, last_name = row
    if _hash_password(password, salt) == pw_hash_db:
        return True, first_name, last_name  
    return False, None, None

def login_page():
    """Render login / register UI"""
    init_users_db()
    st.title("Welcome to Kelly AI")

    # Use columns for side-by-side layout (image and form)
    col1, col2 = st.columns([1, 2])  # Adjusting column width to fit the layout

    with col1:
        # Add an image for the robot character with enhanced styling
        image_path = Path(__file__).parent / "image" / "RobotKelly.png"
        if image_path.exists():
            st.image(str(image_path), use_container_width=True)
        else:
            st.warning("🤖 Robot image not found. Please ensure `image/RobotKelly.png` is included.")
    
    with col2:
        # Customizing form style
        form_style = """
        <style>
            body {
                background-color: #f0f8ff;
                width: 100%;
                height: 100vh;
                display: flex;
            }

            .stImage img {
                max-height: 500px;  /* Set a maximum height for the image */
                width: 100%;    
                height: 400px;    
                object-fit: contain;  
                display: block; 
                
            }

            .stButton>button {
                background: linear-gradient(90deg, #6c63ff, #7f88fc);
                color: white;
                border-radius: 20px;
                padding: 12px 28px;
                font-size: 18px;
                font-weight: bold;
                border: none;
                transition: background 0.3s ease;
            }

            .stButton>button:hover {
                background: linear-gradient(90deg, #7f88fc, #6c63ff);
            }

            /* Add styles for the form submit button specifically */
            .stForm button[type="submit"] {
                background: linear-gradient(90deg, #6c63ff, #7f88fc);
                color: white;
                border-radius: 20px;
                padding: 12px 28px;
                font-size: 18px;
                font-weight: bold;
                border: none;
                transition: background 0.3s ease;
            }

            .stForm button[type="submit"]:hover {
                background: linear-gradient(90deg, #7f88fc, #6c63ff);
            }

            .stTextInput>div>input {
                padding: 12px;
                font-size: 16px;
                border-radius: 8px;
                border: 1px solid #ccc;
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            }

            .stTextInput label {
                font-size: 16px;
            }

            .stForm {
                padding: 20px;
                border-radius: 12px;
                box-shadow: 0 8px 16px rgba(0,0,0,0.1);
            }
        </style>
        """
        st.markdown(form_style, unsafe_allow_html=True)

        # Initialize session state to control form visibility
        if "show_login_form" not in st.session_state:
            st.session_state["show_login_form"] = True

        col1, col2 = st.columns([1, 1])  # Add columns for button layout
        with col1:
            if st.button("Login"):
                st.session_state["show_login_form"] = True  # Show login form
                st.rerun()  # Force the page to rerun after button press
        with col2:
            if st.button("Create New Account"):
                st.session_state["show_login_form"] = False  # Show register form
                st.rerun()  # Force the page to rerun after button press

        if st.session_state["show_login_form"]:
            # Login Form
            st.subheader("Login to Your Account")
            with st.form("login_form"):
                login_user = st.text_input("Email or Username", key="login_user")
                login_pw = st.text_input("Password", type="password", key="login_pw")
                submitted = st.form_submit_button("Login")
                if submitted:
                    is_verified, first_name, last_name = verify_user(login_user, login_pw)
                    if is_verified:
                        # Store the user's information in session state
                        st.session_state["logged_in"] = True
                        st.session_state["username"] = login_user.strip()
                        st.session_state["first_name"] = first_name
                        st.session_state["last_name"] = last_name
                        st.success(f"Welcome back, {first_name} {last_name}!")
                        st.session_state["user_folder"] = os.path.join(BASE_PROJECTS_DIR, login_user)
                        os.makedirs(st.session_state["user_folder"], exist_ok=True)
                        st.switch_page("pages/Ask_Kelly.py")
                    else:
                        st.error("Incorrect username or password.")
        else:
            # Register Form
            st.subheader("Create a New Account")
            with st.form("register_form"):
                reg_first_name = st.text_input("First Name", key="reg_first_name")  # First name input
                reg_last_name = st.text_input("Last Name", key="reg_last_name") 
                reg_user = st.text_input("Email or Username", key="reg_user")
                reg_pw = st.text_input("Password", type="password", key="reg_pw")
                 
                reg_submit = st.form_submit_button("Create Account")
                if reg_submit:
                    ok, msg = create_user(reg_user, reg_pw, reg_first_name, reg_last_name)
                    if ok:
                        st.success("Account created successfully. Please log in.")
                    else:
                        st.error(msg)

def logout_and_rerun():
    for k in ["logged_in", "username", "active_project", "dfs"]:
        if k in st.session_state:
            st.session_state.pop(k)
    st.session_state.clear()
    st.cache_data.clear()

    st.rerun()
