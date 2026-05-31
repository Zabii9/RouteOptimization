import streamlit as st
import mysql.connector
from mysql.connector import Error
import easyocr
from PIL import Image
import io
import datetime
import re

# Database Configuration
DB_CONFIG = {
    'host': 'db42280.public.databaseasp.net',
    'port': 3306,
    'database': 'db42280',
    'user': 'db42280',
    'password': 'admin2233'
}

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        st.error(f"Error connecting to MySQL: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        
        # Create Drivers Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vms_drivers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                phone VARCHAR(50),
                cnic VARCHAR(50),
                status VARCHAR(20) DEFAULT 'Active'
            )
        """)
        
        # Create Salesmen Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vms_salesmen (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                phone VARCHAR(50),
                cnic VARCHAR(50),
                status VARCHAR(20) DEFAULT 'Active'
            )
        """)
        
        # Create Requisitions Table (storing images as MEDIUMBLOB)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vms_requisitions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                partner VARCHAR(100),
                vehicle_type VARCHAR(100),
                driver_id INT,
                salesman_id INT,
                loadform_no VARCHAR(100),
                loadform_date DATE,
                start_meter_reading VARCHAR(100),
                end_meter_reading VARCHAR(100),
                total_km DECIMAL(10,2),
                status VARCHAR(50) DEFAULT 'Pending',
                loadform_image MEDIUMBLOB,
                start_meter_image MEDIUMBLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (driver_id) REFERENCES vms_drivers(id) ON DELETE SET NULL,
                FOREIGN KEY (salesman_id) REFERENCES vms_salesmen(id) ON DELETE SET NULL
            )
        """)
        
        # Add new columns if they don't exist
        try:
            cursor.execute("ALTER TABLE vms_requisitions ADD COLUMN reading_as_per_vendor VARCHAR(100)")
        except Error:
            pass
            
        try:
            cursor.execute("ALTER TABLE vms_requisitions ADD COLUMN end_meter_image MEDIUMBLOB")
        except Error:
            pass
        
        conn.commit()
        cursor.close()
        conn.close()

@st.cache_resource
def get_ocr_reader():
    # Initialize reader once, explicitly disabling GPU for generic environments, unless user has CUDA
    return easyocr.Reader(['en'], gpu=False)

def extract_text_from_image(image_bytes):
    try:
        reader = get_ocr_reader()
        # easyocr can take image bytes directly
        results = reader.readtext(image_bytes, detail=0)
        text = "\n".join(results)
        return text
    except Exception as e:
        st.error(f"OCR Error: {e}")
        return ""

def parse_loadform_number(text):
    # Looking for a generic alphanumeric pattern that might be a loadform number
    # E.g. "LF123456", "Load Form [ D0573LF13480 ]"
    match = re.search(r'(?i)(?:LF|load\s*form)\s*[\[\]:#\-]?\s*([A-Z0-9]+)', text)
    if match:
        return match.group(1)
    
    # Fallback: look for a sequence of capital letters and numbers
    match = re.search(r'\b([A-Z0-9]{6,15})\b', text)
    if match:
        return match.group(1)
    
    return ""

def parse_loadform_date(text):
    # Looking for YYYY-MM-DD or DD/MM/YYYY
    match = re.search(r'\b(\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b', text)
    if match:
        return match.group(1)
    return ""

def parse_meter_reading(text):
    # Look for a large number that could be a meter reading (e.g. 15024)
    # Filter out small numbers like 1, 2
    numbers = re.findall(r'\b\d{3,7}\b', text)
    if numbers:
        return max(numbers) # Return the largest found number as a guess
    return ""

# CRUD operations
def get_all_drivers():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM vms_drivers ORDER BY name")
        drivers = cursor.fetchall()
        conn.close()
        return drivers
    return []

def add_driver(name, phone, cnic, status):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vms_drivers (name, phone, cnic, status) VALUES (%s, %s, %s, %s)",
                       (name, phone, cnic, status))
        conn.commit()
        conn.close()

def delete_driver(driver_id):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vms_drivers WHERE id = %s", (driver_id,))
        conn.commit()
        conn.close()

def get_all_salesmen():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM vms_salesmen ORDER BY name")
        salesmen = cursor.fetchall()
        conn.close()
        return salesmen
    return []

def add_salesman(name, phone, cnic, status):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO vms_salesmen (name, phone, cnic, status) VALUES (%s, %s, %s, %s)",
                       (name, phone, cnic, status))
        conn.commit()
        conn.close()

def delete_salesman(salesman_id):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vms_salesmen WHERE id = %s", (salesman_id,))
        conn.commit()
        conn.close()

def get_all_requisitions():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT r.*, d.name as driver_name, s.name as salesman_name 
            FROM vms_requisitions r
            LEFT JOIN vms_drivers d ON r.driver_id = d.id
            LEFT JOIN vms_salesmen s ON r.salesman_id = s.id
            ORDER BY r.created_at DESC
        """
        cursor.execute(query)
        reqs = cursor.fetchall()
        conn.close()
        return reqs
    return []

def check_loadform_exists(loadform_no):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM vms_requisitions WHERE loadform_no = %s", (loadform_no,))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    return False

def add_requisition(partner, vehicle_type, driver_id, salesman_id, loadform_no, loadform_date, 
                    start_reading, loadform_img, start_reading_img):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        query = """
            INSERT INTO vms_requisitions 
            (partner, vehicle_type, driver_id, salesman_id, loadform_no, loadform_date, 
             start_meter_reading, status, loadform_image, start_meter_image)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Pending', %s, %s)
        """
        # handle None dates
        lf_date_val = loadform_date if loadform_date else None
        
        cursor.execute(query, (partner, vehicle_type, driver_id, salesman_id, loadform_no, lf_date_val,
                               start_reading, loadform_img, start_reading_img))
        conn.commit()
        conn.close()

def update_requisition(req_id, partner, vehicle_type, driver_id, salesman_id, loadform_no, loadform_date, start_reading, end_reading, reading_as_per_vendor):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        query = """
            UPDATE vms_requisitions 
            SET partner=%s, vehicle_type=%s, driver_id=%s, salesman_id=%s, 
                loadform_no=%s, loadform_date=%s, start_meter_reading=%s, end_meter_reading=%s, reading_as_per_vendor=%s
            WHERE id=%s
        """
        lf_date_val = loadform_date if loadform_date else None
        cursor.execute(query, (partner, vehicle_type, driver_id, salesman_id, loadform_no, lf_date_val, start_reading, end_reading, reading_as_per_vendor, req_id))
        conn.commit()
        conn.close()

def complete_requisition(req_id, end_reading, reading_as_per_vendor, end_meter_img):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        # Get start reading to calculate KM
        cursor.execute("SELECT start_meter_reading FROM vms_requisitions WHERE id = %s", (req_id,))
        row = cursor.fetchone()
        
        total_km = None
        if row and row['start_meter_reading']:
            try:
                start = float(row['start_meter_reading'])
                end = float(end_reading)
                total_km = end - start
            except ValueError:
                pass
                
        cursor.execute("""
            UPDATE vms_requisitions 
            SET end_meter_reading = %s, status = 'Completed', total_km = %s,
                reading_as_per_vendor = %s, end_meter_image = %s
            WHERE id = %s
        """, (end_reading, total_km, reading_as_per_vendor, end_meter_img, req_id))
        conn.commit()
        conn.close()

# --- UI Components ---

def render_driver_salesman_crud():
    st.markdown("## 👥 Driver & Salesman CRUD")
    
    tab1, tab2 = st.tabs(["Drivers", "Salesmen"])
    
    with tab1:
        st.subheader("Drivers")
        col1, col2 = st.columns([1, 2])
        
        with col1:
            with st.form("add_driver_form", clear_on_submit=True):
                st.write("Add New Driver")
                d_name = st.text_input("Name*")
                d_phone = st.text_input("Phone")
                d_cnic = st.text_input("CNIC")
                d_status = st.selectbox("Status", ["Active", "Inactive"])
                
                if st.form_submit_button("Add Driver"):
                    if d_name:
                        add_driver(d_name, d_phone, d_cnic, d_status)
                        st.success("Driver added successfully!")
                        st.rerun()
                    else:
                        st.error("Name is required.")
                        
        with col2:
            drivers = get_all_drivers()
            if drivers:
                h1, h2, h3, h4, h5 = st.columns([2, 2, 2, 1, 1])
                h1.markdown("**Name**")
                h2.markdown("**Phone**")
                h3.markdown("**CNIC**")
                h4.markdown("**Status**")
                h5.markdown("**Action**")
                st.markdown("---")
                
                for d in drivers:
                    c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 1, 1])
                    c1.write(d['name'])
                    c2.write(d['phone'])
                    c3.write(d['cnic'])
                    c4.write(d['status'])
                    if c5.button("Delete", key=f"del_d_{d['id']}"):
                        delete_driver(d['id'])
                        st.rerun()
            else:
                st.info("No drivers found.")

    with tab2:
        st.subheader("Salesmen")
        col1, col2 = st.columns([1, 2])
        
        with col1:
            with st.form("add_salesman_form", clear_on_submit=True):
                st.write("Add New Salesman")
                s_name = st.text_input("Name*")
                s_phone = st.text_input("Phone")
                s_cnic = st.text_input("CNIC")
                s_status = st.selectbox("Status", ["Active", "Inactive"])
                
                if st.form_submit_button("Add Salesman"):
                    if s_name:
                        add_salesman(s_name, s_phone, s_cnic, s_status)
                        st.success("Salesman added successfully!")
                        st.rerun()
                    else:
                        st.error("Name is required.")
                        
        with col2:
            salesmen = get_all_salesmen()
            if salesmen:
                h1, h2, h3, h4, h5 = st.columns([2, 2, 2, 1, 1])
                h1.markdown("**Name**")
                h2.markdown("**Phone**")
                h3.markdown("**CNIC**")
                h4.markdown("**Status**")
                h5.markdown("**Action**")
                st.markdown("---")
                
                for s in salesmen:
                    c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 1, 1])
                    c1.write(s['name'])
                    c2.write(s['phone'])
                    c3.write(s['cnic'])
                    c4.write(s['status'])
                    if c5.button("Delete", key=f"del_s_{s['id']}"):
                        delete_salesman(s['id'])
                        st.rerun()
            else:
                st.info("No salesmen found.")


def render_create_requisition():
    st.markdown("## 🚛 Create Vehicle Requisition")
    
    # Needs driver and salesman data
    drivers = get_all_drivers()
    salesmen = get_all_salesmen()
    
    if not drivers or not salesmen:
        st.warning("Please add at least one Driver and one Salesman before creating a requisition.")
        return
        
    driver_options = {d['name']: d['id'] for d in drivers if d['status'] == 'Active'}
    salesman_options = {s['name']: s['id'] for s in salesmen if s['status'] == 'Active'}
    
    # Initialize a key to clear components
    if "req_key" not in st.session_state: st.session_state.req_key = 0
    rk = st.session_state.req_key
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        partner = st.selectbox("Partner", ["CBL", "Olpers Lhr", "Olper KHI", "Tapal"], key=f"partner_{rk}")
        vehicle_type = st.selectbox("Vehicle Type*", ["Suzuki", "Shehzore", "Mazda"], key=f"vtype_{rk}")
        driver_name = st.selectbox("Driver Name*", list(driver_options.keys()), key=f"driver_{rk}")
        salesman_name = st.selectbox("Salesman Name*", list(salesman_options.keys()), key=f"salesman_{rk}")
        
        lf_pic = st.file_uploader("LoadForm Picture*", type=["png", "jpg", "jpeg"], key=f"lf_pic_{rk}")
        start_pic = st.file_uploader("Start Reading Picture*", type=["png", "jpg", "jpeg"], key=f"start_pic_{rk}")

    with col2:
        st.markdown("### Extracted Information (Edit if incorrect)")
        
        # State variables for extracted info
        if "lf_no_val" not in st.session_state: st.session_state.lf_no_val = ""
        if "lf_date_val" not in st.session_state: st.session_state.lf_date_val = ""
        if "meter_val" not in st.session_state: st.session_state.meter_val = ""
        
        if lf_pic:
            if st.button("Extract Data from LoadForm", key=f"ext_lf_{rk}"):
                with st.spinner("Extracting text with OCR..."):
                    bytes_data = lf_pic.getvalue()
                    text = extract_text_from_image(bytes_data)
                    st.session_state.lf_no_val = parse_loadform_number(text)
                    st.session_state.lf_date_val = parse_loadform_date(text)
                    st.rerun()
                    
        if start_pic:
            if st.button("Extract Data from Meter", key=f"ext_meter_{rk}"):
                with st.spinner("Extracting meter reading with OCR..."):
                    bytes_data = start_pic.getvalue()
                    text = extract_text_from_image(bytes_data)
                    st.session_state.meter_val = parse_meter_reading(text)
                    st.rerun()

        with st.form(f"requisition_form_{rk}", clear_on_submit=True):
            loadform_no = st.text_input("LoadForm #*", value=st.session_state.lf_no_val)
            
            # Use string input for date to handle fallback or weird parsed formats easily, 
            # or try to parse into datetime if possible
            lf_date_str = st.text_input("LoadForm Date* (YYYY-MM-DD)", value=st.session_state.lf_date_val)
            
            start_reading = st.text_input("Start Meter Reading*", value=st.session_state.meter_val)
            
            submitted = st.form_submit_button("Submit Requisition")
            if submitted:
                if not loadform_no:
                    st.error("LoadForm # is required to check duplicates.")
                elif check_loadform_exists(loadform_no):
                    st.error("already data exist")
                elif not (lf_pic and start_pic and loadform_no and lf_date_str and start_reading):
                    st.error("Please fill all required fields and upload both pictures.")
                else:
                    # Parse date safely
                    parsed_date = None
                    try:
                        # simple parsing attempt based on our regex output
                        if '-' in lf_date_str:
                            parsed_date = datetime.datetime.strptime(lf_date_str, "%Y-%m-%d").date()
                        elif '/' in lf_date_str:
                            parsed_date = datetime.datetime.strptime(lf_date_str, "%d/%m/%Y").date()
                    except:
                        pass
                    
                    if not parsed_date:
                        # Try fallback
                        try:
                            # if it's somehow valid iso format
                            parsed_date = datetime.date.fromisoformat(lf_date_str)
                        except:
                            st.warning("Could not parse date correctly, will save as NULL or might fail.")
                            
                    add_requisition(
                        partner, vehicle_type, 
                        driver_options[driver_name], salesman_options[salesman_name],
                        loadform_no, parsed_date, start_reading, 
                        lf_pic.getvalue(), start_pic.getvalue()
                    )
                    st.success("Requisition created successfully! It is now in Pending status.")
                    
                    # Reset state to clear form and uploaders
                    st.session_state.lf_no_val = ""
                    st.session_state.lf_date_val = ""
                    st.session_state.meter_val = ""
                    st.session_state.req_key += 1
                    
                    st.rerun()


def render_view_requisitions():
    st.markdown("## 📋 View Vehicle Requisitions")
    
    reqs = get_all_requisitions()
    
    if not reqs:
        st.info("No requisitions found.")
        return
    if "view_req_id" not in st.session_state:
        st.session_state.view_req_id = None
        
    if "edit_req_id" not in st.session_state:
        st.session_state.edit_req_id = None
        
    if st.session_state.edit_req_id is not None:
        if st.button("⬅️ Back to Table", key="back_from_edit"):
            st.session_state.edit_req_id = None
            st.rerun()
            
        r = next((req for req in reqs if req['id'] == st.session_state.edit_req_id), None)
        if not r:
            st.error("Requisition not found.")
            st.session_state.edit_req_id = None
            return
            
        st.markdown(f"### ✏️ Edit Requisition #{r['id']}")
        
        drivers = get_all_drivers()
        salesmen = get_all_salesmen()
        driver_options = {d['name']: d['id'] for d in drivers}
        salesman_options = {s['name']: s['id'] for s in salesmen}
        
        with st.form(f"edit_req_form_{r['id']}"):
            partner = st.selectbox("Partner", ["CBL", "Olpers Lhr", "Olper KHI", "Tapal"], 
                                   index=["CBL", "Olpers Lhr", "Olper KHI", "Tapal"].index(r['partner']) if r['partner'] in ["CBL", "Olpers Lhr", "Olper KHI", "Tapal"] else 0)
            vehicle_type = st.selectbox("Vehicle Type*", ["Suzuki", "Shehzore", "Mazda"], 
                                        index=["Suzuki", "Shehzore", "Mazda"].index(r['vehicle_type']) if r['vehicle_type'] in ["Suzuki", "Shehzore", "Mazda"] else 0)
            
            d_index = list(driver_options.keys()).index(r['driver_name']) if r['driver_name'] in driver_options else 0
            s_index = list(salesman_options.keys()).index(r['salesman_name']) if r['salesman_name'] in salesman_options else 0
            
            driver_name = st.selectbox("Driver Name*", list(driver_options.keys()), index=d_index)
            salesman_name = st.selectbox("Salesman Name*", list(salesman_options.keys()), index=s_index)
            
            loadform_no = st.text_input("LoadForm #*", value=r['loadform_no'])
            lf_date_str = st.text_input("LoadForm Date* (YYYY-MM-DD)", value=str(r['loadform_date']) if r['loadform_date'] else "")
            start_reading = st.text_input("Start Meter Reading*", value=r['start_meter_reading'])
            end_reading = st.text_input("End Meter Reading*", value=r['end_meter_reading']) 
            reading_as_per_vendor = st.text_input("Reading as Per Vendor*", value=r['reading_as_per_vendor'])


            if st.form_submit_button("Save Changes"):
                parsed_date = None
                try:
                    parsed_date = datetime.date.fromisoformat(lf_date_str)
                except:
                    pass
                
                update_requisition(
                    r['id'], partner, vehicle_type, 
                    driver_options[driver_name], salesman_options[salesman_name],
                    loadform_no, parsed_date, start_reading, end_reading, reading_as_per_vendor
                )
                st.success("Updated successfully!")
                st.session_state.edit_req_id = None
                st.rerun()
        return

    if st.session_state.view_req_id is not None:
        if st.button("⬅️ Back to Table"):
            st.session_state.view_req_id = None
            st.rerun()
            
        r = next((req for req in reqs if req['id'] == st.session_state.view_req_id), None)
        if not r:
            st.error("Requisition not found.")
            st.session_state.view_req_id = None
            return
            
        st.markdown(f"### Requisition #{r['id']} | {r['loadform_no']} | {r['partner']} | Status: {r['status']}")
        
        c1, c2, c3, c4 = st.columns(4)
        c1.write(f"**Vehicle:** {r['vehicle_type']}")
        c1.write(f"**Driver:** {r['driver_name']}")
        c1.write(f"**Salesman:** {r['salesman_name']}")
        
        c2.write(f"**LoadForm Date:** {r['loadform_date']}")
        c2.write(f"**Start Meter:** {r['start_meter_reading']}")
        
        if r['status'] == 'Completed':
            c2.write(f"**End Meter:** {r['end_meter_reading']}")
            c2.write(f"**Total KM:** {r['total_km']}")
            if r.get('reading_as_per_vendor'):
                c2.write(f"**Vendor Reading:** {r['reading_as_per_vendor']}")
        
        c3.write("**LF Image:**")
        if r['loadform_image']:
            c3.image(r['loadform_image'], width=150)
        else:
            c3.write("No Image")
            
        c4.write("**Start Meter Image:**")
        if r['start_meter_image']:
            c4.image(r['start_meter_image'], width=150)
        else:
            c4.write("No Image")

        if r['status'] == 'Completed' and r.get('end_meter_image'):
            st.write("**End Meter Image:**")
            st.image(r['end_meter_image'], width=150)
            
        if r['status'] == 'Pending':
            st.markdown("---")
            with st.form(f"complete_req_{r['id']}"):
                end_reading = st.text_input("Enter End Meter Reading to Complete:")
                reading_as_per_vendor = st.text_input("Reading as Per Vendor:")
                end_meter_img = st.file_uploader("End Meter Picture*", type=["png", "jpg", "jpeg"])
                
                if st.form_submit_button("Complete Requisition"):
                    if end_reading and end_meter_img:
                        complete_requisition(r['id'], end_reading, reading_as_per_vendor, end_meter_img.getvalue())
                        st.success(f"Requisition #{r['id']} completed!")
                        # Clear view selection so it returns to table
                        st.session_state.view_req_id = None
                        st.rerun()
                    else:
                        st.error("Please enter the end reading and upload the end meter image.")
    else:
        st.markdown(
            '''
            <style>
            .req-header { font-weight: bold; padding-bottom: 10px; border-bottom: 2px solid #ddd; margin-bottom: 10px; }
            </style>
            ''', unsafe_allow_html=True
        )
        h1, h2, h3, h4, h5, h6 = st.columns([1, 2, 2, 2, 2, 1])
        h1.markdown('<div class="req-header">ID</div>', unsafe_allow_html=True)
        h2.markdown('<div class="req-header">LoadForm #</div>', unsafe_allow_html=True)
        h3.markdown('<div class="req-header">Date</div>', unsafe_allow_html=True)
        h4.markdown('<div class="req-header">Partner</div>', unsafe_allow_html=True)
        h5.markdown('<div class="req-header">Status</div>', unsafe_allow_html=True)
        h6.markdown('<div class="req-header">Action</div>', unsafe_allow_html=True)
        
        for r in reqs:
            c1, c2, c3, c4, c5, c6 = st.columns([1, 2, 2, 2, 2, 1])
            c1.write(r['id'])
            c2.write(r['loadform_no'])
            c3.write(r['loadform_date'])
            c4.write(r['partner'])
            c5.write(r['status'])
            
            btn_col1, btn_col2 = c6.columns(2)
            if btn_col1.button("👁️", key=f"view_btn_{r['id']}", help="View Details"):
                st.session_state.view_req_id = r['id']
                st.rerun()
            if btn_col2.button("✏️", key=f"edit_btn_{r['id']}", help="Edit Requisition"):
                st.session_state.edit_req_id = r['id']
                st.rerun()
