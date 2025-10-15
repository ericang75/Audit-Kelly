import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path
import io

# Page config
st.set_page_config(
    page_title="Accounts Payable Analyzer",
    page_icon="📊",
    layout="wide"
)

# Initialize session state for mappings
if 'mappings' not in st.session_state:
    st.session_state.mappings = {}
if 'history' not in st.session_state:
    st.session_state.history = []

# Load/Save mappings
def load_mappings():
    try:
        with open('ap_mappings.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_mappings(mappings):
    with open('ap_mappings.json', 'w') as f:
        json.dump(mappings, f, indent=2)
    st.session_state.mappings = mappings

# ==================== CORE ANALYSIS FUNCTIONS ====================

# AP01: Aging By Invoice Date
def ap01_aging_analysis(df, date_field, amount_field, cutoff_date, periods='30,60,90,120'):
    """Age invoices based on invoice date"""
    df = df.copy()
    df[date_field] = pd.to_datetime(df[date_field])
    cutoff = pd.to_datetime(cutoff_date)
    
    df['Days_Old'] = (cutoff - df[date_field]).dt.days
    
    period_list = [int(p) for p in periods.split(',')]
    
    def assign_bucket(days):
        if days < 0:
            return 'Future'
        for i, period in enumerate(period_list):
            if days <= period:
                prev = period_list[i-1] if i > 0 else 0
                return f'{prev+1}-{period} days'
        return f'Over {period_list[-1]} days'
    
    df['Age_Bucket'] = df['Days_Old'].apply(assign_bucket)
    
    summary = df.groupby('Age_Bucket')[amount_field].agg(['sum', 'count']).reset_index()
    summary.columns = ['Age_Bucket', 'Total_Amount', 'Count']
    
    return df, summary

# AP02: Duplicate Invoices Detection
def ap02_find_duplicates(df, vendor_field, invoice_field, date_field, amount_field, 
                         test_type='exact', tolerance=0):
    """Find duplicate invoices based on various criteria"""
    df = df.copy()
    
    if test_type == 'exact':
        subset = [vendor_field, invoice_field, date_field, amount_field]
        duplicates = df[df.duplicated(subset=subset, keep=False)]
        
    elif test_type == 'near_invoice':
        df[date_field] = pd.to_datetime(df[date_field])
        df = df.sort_values([vendor_field, invoice_field, date_field])
        
        duplicates_list = []
        for (vendor, invoice), group in df.groupby([vendor_field, invoice_field]):
            if len(group) > 1:
                for i in range(len(group)):
                    for j in range(i+1, len(group)):
                        date_diff = abs((group.iloc[i][date_field] - group.iloc[j][date_field]).days)
                        amount_match = group.iloc[i][amount_field] == group.iloc[j][amount_field]
                        if date_diff <= tolerance and amount_match:
                            duplicates_list.extend([group.iloc[i], group.iloc[j]])
        
        duplicates = pd.DataFrame(duplicates_list).drop_duplicates() if duplicates_list else pd.DataFrame()
            
    elif test_type == 'near_date':
        df[date_field] = pd.to_datetime(df[date_field])
        df = df.sort_values([vendor_field, invoice_field, amount_field, date_field])
        
        duplicates_list = []
        for (vendor, invoice, amount), group in df.groupby([vendor_field, invoice_field, amount_field]):
            if len(group) > 1:
                for i in range(len(group)):
                    for j in range(i+1, len(group)):
                        date_diff = abs((group.iloc[i][date_field] - group.iloc[j][date_field]).days)
                        if date_diff <= tolerance:
                            duplicates_list.extend([group.iloc[i], group.iloc[j]])
        
        duplicates = pd.DataFrame(duplicates_list).drop_duplicates() if duplicates_list else pd.DataFrame()
            
    elif test_type == 'similar_vendor':
        subset = [invoice_field, date_field, amount_field]
        duplicates = df[df.duplicated(subset=subset, keep=False)]
        
    elif test_type == 'similar_invoice':
        subset = [vendor_field, date_field, amount_field]
        duplicates = df[df.duplicated(subset=subset, keep=False)]
    
    return duplicates

# AP03: Creditors With Net Debit Balances
def ap03_debit_balances(df, vendor_field, amount_field):
    """Find creditors with net debit balances"""
    summary = df.groupby(vendor_field)[amount_field].sum().reset_index()
    summary.columns = [vendor_field, 'Net_Balance']
    
    debit_balances = summary[summary['Net_Balance'] > 0].copy()
    debit_balances = debit_balances.sort_values('Net_Balance', ascending=False)
    
    return debit_balances

# AP04: Creditors With Balances > Credit Limit
def ap04_exceeds_limit(df_transactions, df_limits, 
                       vendor_field_trans, amount_field,
                       vendor_field_limit, limit_field):
    """Find creditors whose balance exceeds credit limit"""
    balances = df_transactions.groupby(vendor_field_trans)[amount_field].sum().reset_index()
    balances.columns = [vendor_field_trans, 'Current_Balance']
    
    merged = balances.merge(
        df_limits[[vendor_field_limit, limit_field]], 
        left_on=vendor_field_trans, 
        right_on=vendor_field_limit,
        how='inner'
    )
    
    exceeds = merged[merged['Current_Balance'] > merged[limit_field]].copy()
    exceeds['Excess_Amount'] = exceeds['Current_Balance'] - exceeds[limit_field]
    exceeds = exceeds.sort_values('Excess_Amount', ascending=False)
    
    return exceeds

# AP05: Total Amounts > Credit Limit (with date range)
def ap05_exceeds_limit_period(df_transactions, df_limits,
                               vendor_field_trans, amount_field, date_field,
                               vendor_field_limit, limit_field,
                               start_date, end_date):
    """Find creditors whose total amounts in period exceed credit limit"""
    df = df_transactions.copy()
    df[date_field] = pd.to_datetime(df[date_field])
    
    mask = (df[date_field] >= pd.to_datetime(start_date)) & (df[date_field] <= pd.to_datetime(end_date))
    df_filtered = df[mask]
    
    totals = df_filtered.groupby(vendor_field_trans)[amount_field].sum().reset_index()
    totals.columns = [vendor_field_trans, 'Total_Amount']
    
    merged = totals.merge(
        df_limits[[vendor_field_limit, limit_field]],
        left_on=vendor_field_trans,
        right_on=vendor_field_limit,
        how='inner'
    )
    
    exceeds = merged[merged['Total_Amount'] > merged[limit_field]].copy()
    exceeds['Excess_Amount'] = exceeds['Total_Amount'] - exceeds[limit_field]
    exceeds = exceeds.sort_values('Excess_Amount', ascending=False)
    
    return exceeds

# AP06: Creditor Transaction Summary
def ap06_creditor_summary(df, vendor_field, selected_vendor):
    """Extract all transactions for a specific creditor"""
    result = df[df[vendor_field] == selected_vendor].copy()
    return result

# AP07: Invoices Without PO
def ap07_invoices_without_po(df, po_field):
    """Find invoices without purchase orders"""
    result = df[df[po_field].isna() | (df[po_field] == '') | (df[po_field].astype(str).str.strip() == '')].copy()
    return result

# AP08: Transactions Around Specified Date
def ap08_transactions_around_date(df, date_field, target_date, days_range):
    """Find transactions within N days of specified date"""
    df = df.copy()
    df[date_field] = pd.to_datetime(df[date_field])
    target = pd.to_datetime(target_date)
    
    df['Days_Difference'] = abs((df[date_field] - target).dt.days)
    result = df[df['Days_Difference'] <= days_range].copy()
    result = result.sort_values('Days_Difference')
    
    return result

# AP09: Transactions Posted On Specific Dates
def ap09_transactions_date_range(df, date_field, start_date, end_date):
    """Find transactions within date range"""
    df = df.copy()
    df[date_field] = pd.to_datetime(df[date_field])
    
    mask = (df[date_field] >= pd.to_datetime(start_date)) & (df[date_field] <= pd.to_datetime(end_date))
    result = df[mask].copy()
    
    return result

# AP10: Transactions Posted At Specific Times
def ap10_transactions_time_range(df, datetime_field, start_time, end_time):
    """Find transactions within time range"""
    df = df.copy()
    df[datetime_field] = pd.to_datetime(df[datetime_field])
    
    df['Time_Only'] = df[datetime_field].dt.time
    
    start_t = datetime.strptime(start_time, '%H:%M:%S').time()
    end_t = datetime.strptime(end_time, '%H:%M:%S').time()
    
    if start_t <= end_t:
        result = df[(df['Time_Only'] >= start_t) & (df['Time_Only'] <= end_t)].copy()
    else:
        result = df[(df['Time_Only'] >= start_t) | (df['Time_Only'] <= end_t)].copy()
    
    return result

# AP11: Transactions By UserID
def ap11_transactions_by_user(df, user_field, selected_user):
    """Extract transactions by specific user"""
    result = df[df[user_field] == selected_user].copy()
    return result

# AP12: Transactions On Weekends
def ap12_weekend_transactions(df, date_field, weekend_type='sat_sun'):
    """Find transactions posted on weekends"""
    df = df.copy()
    df[date_field] = pd.to_datetime(df[date_field])
    df['DayOfWeek'] = df[date_field].dt.dayofweek
    
    if weekend_type == 'sat_sun':
        result = df[df['DayOfWeek'].isin([5, 6])].copy()
    else:  # sun_mon
        result = df[df['DayOfWeek'].isin([6, 0])].copy()
    
    return result

# AP13: Transactions With Rounded Amounts
def ap13_rounded_amounts(df, amount_field):
    """Find transactions with rounded amounts (no decimals)"""
    df = df.copy()
    result = df[df[amount_field] == df[amount_field].round()].copy()
    return result

# AP14: Duplicate Field Search
def ap14_duplicate_fields(df, fields, error_limit=20):
    """Find duplicates based on up to 4 fields"""
    fields = [f for f in fields if f]
    
    if not fields:
        return pd.DataFrame()
    
    duplicates = df[df.duplicated(subset=fields, keep=False)].copy()
    
    if error_limit and len(duplicates) > error_limit:
        st.warning(f"Found {len(duplicates)} duplicates, showing first {error_limit}")
        duplicates = duplicates.head(error_limit)
    
    return duplicates

# ==================== UI COMPONENTS ====================

def render_sidebar():
    st.sidebar.title("📊 AP Analyzer")
    st.sidebar.markdown("---")
    
    analysis_category = st.sidebar.radio(
        "Category",
        ["Account Payable", "Account Receivable", "Special Checks"]
    )
    
    if analysis_category == "Account Payable":
        analysis_options = [
            "AP01 - Aging by Invoice Date",
            "AP02 - Duplicate Invoices",
            "AP03 - Net Debit Balances",
            "AP04 - Balances > Credit Limit",
            "AP05 - Period Amounts > Limit",
            "AP06 - Creditor Transaction Summary",
            "AP07 - Invoices Without PO",
            "AP08 - Transactions Around Date",
            "AP09 - Transactions in Date Range",
            "AP10 - Transactions by Time",
            "AP11 - Transactions by UserID",
            "AP12 - Weekend Transactions",
            "AP13 - Rounded Amounts",
            "AP14 - Duplicate Field Search",
        ]
    elif analysis_category == "Account Receivable":
        analysis_options = [
            "AR01 - Aging by Invoice Date",
        ]
    else:  
        analysis_options = [
            "GL01 - Transactions by Amount",
        ]

    analysis_type = st.sidebar.selectbox("Select Analysis", analysis_options)
    
    st.sidebar.markdown("---")
    
    if st.sidebar.button("📥 Load Saved Mappings"):
        loaded = load_mappings()
        if loaded:
            st.session_state.mappings = loaded
            st.sidebar.success(f"Loaded {len(loaded)} mappings")
        else:
            st.sidebar.info("No saved mappings found")
    
    if st.sidebar.button("💾 Save Current Mappings"):
        save_mappings(st.session_state.mappings)
        st.sidebar.success("Mappings saved!")
    
    if st.sidebar.button("🗑️ Clear All Mappings"):
        st.session_state.mappings = {}
        st.sidebar.warning("Mappings cleared")
    
    return analysis_type

def get_saved_mapping(analysis_key):
    return st.session_state.mappings.get(analysis_key, {})

def save_current_mapping(analysis_key, mapping):
    st.session_state.mappings[analysis_key] = mapping

# ==================== MAIN APP ====================

def main():
    analysis_type = render_sidebar()
    
    st.title("🏦 Accounts Payable Analysis Tool")
    st.markdown("Python-powered alternative to Arbutus Analyzer procedures")
    
    saved = get_saved_mapping(analysis_type)
    
    # ==================== AP01 - AGING ====================
    if analysis_type == "AP01 - Aging by Invoice Date":
        st.header("📅 AP01: Aging by Invoice Date")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                date_field = st.selectbox("Invoice Date Field", df.columns, 
                                         index=df.columns.tolist().index(saved.get('date_field', df.columns[0])) if saved.get('date_field') in df.columns else 0)
            with col2:
                amount_field = st.selectbox("Amount Field", df.select_dtypes(include=['number']).columns,
                                           index=df.select_dtypes(include=['number']).columns.tolist().index(saved.get('amount_field', df.select_dtypes(include=['number']).columns[0])) if saved.get('amount_field') in df.select_dtypes(include=['number']).columns else 0)
            with col3:
                cutoff_date = st.date_input("Cutoff Date", value=datetime.now())
            
            periods = st.text_input("Aging Periods (comma-separated days)", value=saved.get('periods', "30,60,90,120"))
            
            if st.button("🚀 Run Aging Analysis", type="primary"):
                save_current_mapping(analysis_type, {
                    'date_field': date_field,
                    'amount_field': amount_field,
                    'periods': periods
                })
                
                with st.spinner("Analyzing..."):
                    result_df, summary = ap01_aging_analysis(df, date_field, amount_field, cutoff_date, periods)
                
                st.success("✅ Analysis Complete!")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.subheader("📊 Aging Summary")
                    st.dataframe(summary, use_container_width=True)
                    
                with col2:
                    st.subheader("📈 Visualization")
                    st.bar_chart(summary.set_index('Age_Bucket')['Total_Amount'])
                
                st.subheader("📋 Detailed Results")
                st.dataframe(result_df, use_container_width=True)
                
                csv = result_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download Full Results",
                    data=csv,
                    file_name=f"AP01_Aging_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
    
    # ==================== AP02 - DUPLICATES ====================
    elif analysis_type == "AP02 - Duplicate Invoices":
        st.header("🔍 AP02: Duplicate Invoices Detection")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                vendor_field = st.selectbox("Vendor Field", df.columns,
                                           index=df.columns.tolist().index(saved.get('vendor_field', df.columns[0])) if saved.get('vendor_field') in df.columns else 0)
            with col2:
                invoice_field = st.selectbox("Invoice Field", df.columns,
                                            index=df.columns.tolist().index(saved.get('invoice_field', df.columns[1])) if saved.get('invoice_field') in df.columns else min(1, len(df.columns)-1))
            with col3:
                date_field = st.selectbox("Date Field", df.columns,
                                         index=df.columns.tolist().index(saved.get('date_field', df.columns[2])) if saved.get('date_field') in df.columns else min(2, len(df.columns)-1))
            with col4:
                amount_field = st.selectbox("Amount Field", df.select_dtypes(include=['number']).columns,
                                           index=df.select_dtypes(include=['number']).columns.tolist().index(saved.get('amount_field', df.select_dtypes(include=['number']).columns[0])) if saved.get('amount_field') in df.select_dtypes(include=['number']).columns else 0)
            
            st.markdown("---")
            
            test_type = st.selectbox(
                "Duplicate Test Type",
                ["exact", "near_invoice", "near_date", "similar_vendor", "similar_invoice"],
                format_func=lambda x: {
                    "exact": "Exact Duplicates (All fields match)",
                    "near_invoice": "Near Duplicates on Invoice (within date tolerance)",
                    "near_date": "Near Duplicates on Date (within tolerance)",
                    "similar_vendor": "Similar Vendor Names",
                    "similar_invoice": "Similar Invoice Numbers"
                }[x]
            )
            
            tolerance = 0
            if test_type in ['near_invoice', 'near_date']:
                tolerance = st.number_input("Tolerance (days)", min_value=0, value=saved.get('tolerance', 5))
            
            if st.button("🔍 Find Duplicates", type="primary"):
                save_current_mapping(analysis_type, {
                    'vendor_field': vendor_field,
                    'invoice_field': invoice_field,
                    'date_field': date_field,
                    'amount_field': amount_field,
                    'tolerance': tolerance
                })
                
                with st.spinner("Searching for duplicates..."):
                    duplicates = ap02_find_duplicates(df, vendor_field, invoice_field, date_field, amount_field, test_type, tolerance)
                
                if len(duplicates) > 0:
                    st.error(f"⚠️ Found {len(duplicates)} potential duplicate records!")
                    st.dataframe(duplicates, use_container_width=True)
                    
                    csv = duplicates.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Duplicates",
                        data=csv,
                        file_name=f"AP02_Duplicates_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.success("✅ No duplicates found!")
    
    # ==================== AP03 - DEBIT BALANCES ====================
    elif analysis_type == "AP03 - Net Debit Balances":
        st.header("💰 AP03: Creditors With Net Debit Balances")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            col1, col2 = st.columns(2)
            
            with col1:
                vendor_field = st.selectbox("Vendor Field", df.columns,
                                           index=df.columns.tolist().index(saved.get('vendor_field', df.columns[0])) if saved.get('vendor_field') in df.columns else 0)
            with col2:
                amount_field = st.selectbox("Amount Field", df.select_dtypes(include=['number']).columns,
                                           index=df.select_dtypes(include=['number']).columns.tolist().index(saved.get('amount_field', df.select_dtypes(include=['number']).columns[0])) if saved.get('amount_field') in df.select_dtypes(include=['number']).columns else 0)
            
            if st.button("🔍 Find Debit Balances", type="primary"):
                save_current_mapping(analysis_type, {
                    'vendor_field': vendor_field,
                    'amount_field': amount_field
                })
                
                with st.spinner("Analyzing..."):
                    debit_balances = ap03_debit_balances(df, vendor_field, amount_field)
                
                if len(debit_balances) > 0:
                    st.warning(f"⚠️ Found {len(debit_balances)} creditors with debit balances")
                    
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        st.dataframe(debit_balances, use_container_width=True)
                    with col2:
                        st.metric("Total Debit Amount", f"${debit_balances['Net_Balance'].sum():,.2f}")
                        st.metric("Number of Creditors", len(debit_balances))
                    
                    csv = debit_balances.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"AP03_Debit_Balances_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.success("✅ No creditors with debit balances found!")
    
    # ==================== AP04 & AP05 - CREDIT LIMIT ====================
    elif analysis_type in ["AP04 - Balances > Credit Limit", "AP05 - Period Amounts > Limit"]:
        is_ap05 = "AP05" in analysis_type
        st.header(f"🚨 {analysis_type}")
        
        col1, col2 = st.columns(2)
        
        with col1:
            trans_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'], key="trans")
        with col2:
            limit_file = st.file_uploader("Upload Credit Limits File", type=['csv', 'xlsx'], key="limit")
        
        if trans_file and limit_file:
            df_trans = pd.read_csv(trans_file) if trans_file.name.endswith('.csv') else pd.read_excel(trans_file)
            df_limits = pd.read_csv(limit_file) if limit_file.name.endswith('.csv') else pd.read_excel(limit_file)
            
            st.success(f"✅ Loaded {len(df_trans):,} transactions and {len(df_limits):,} credit limits")
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Transactions Preview")
                st.dataframe(df_trans.head(3), use_container_width=True)
            with col2:
                st.subheader("Credit Limits Preview")
                st.dataframe(df_limits.head(3), use_container_width=True)
            
            st.markdown("---")
            st.subheader("Field Mapping")
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                vendor_trans = st.selectbox("Vendor Field (Transactions)", df_trans.columns,
                                           index=df_trans.columns.tolist().index(saved.get('vendor_trans', df_trans.columns[0])) if saved.get('vendor_trans') in df_trans.columns else 0)
                amount_field = st.selectbox("Amount Field", df_trans.select_dtypes(include=['number']).columns,
                                           index=df_trans.select_dtypes(include=['number']).columns.tolist().index(saved.get('amount_field', df_trans.select_dtypes(include=['number']).columns[0])) if saved.get('amount_field') in df_trans.select_dtypes(include=['number']).columns else 0)
            
            with col2:
                vendor_limit = st.selectbox("Vendor Field (Limits)", df_limits.columns,
                                           index=df_limits.columns.tolist().index(saved.get('vendor_limit', df_limits.columns[0])) if saved.get('vendor_limit') in df_limits.columns else 0)
                limit_field = st.selectbox("Credit Limit Field", df_limits.select_dtypes(include=['number']).columns,
                                          index=df_limits.select_dtypes(include=['number']).columns.tolist().index(saved.get('limit_field', df_limits.select_dtypes(include=['number']).columns[0])) if saved.get('limit_field') in df_limits.select_dtypes(include=['number']).columns else 0)
            
            with col3:
                if is_ap05:
                    date_field = st.selectbox("Date Field", df_trans.columns,
                                             index=df_trans.columns.tolist().index(saved.get('date_field', df_trans.columns[0])) if saved.get('date_field') in df_trans.columns else 0)
                    start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=30))
                    end_date = st.date_input("End Date", value=datetime.now())
            
            if st.button("🔍 Check Credit Limits", type="primary"):
                mapping_data = {
                    'vendor_trans': vendor_trans,
                    'vendor_limit': vendor_limit,
                    'amount_field': amount_field,
                    'limit_field': limit_field
                }
                
                if is_ap05:
                    mapping_data.update({
                        'date_field': date_field,
                        'start_date': str(start_date),
                        'end_date': str(end_date)
                    })
                
                save_current_mapping(analysis_type, mapping_data)
                
                with st.spinner("Analyzing credit limits..."):
                    if is_ap05:
                        exceeds = ap05_exceeds_limit_period(
                            df_trans, df_limits, vendor_trans, amount_field, date_field,
                            vendor_limit, limit_field, start_date, end_date
                        )
                    else:
                        exceeds = ap04_exceeds_limit(
                            df_trans, df_limits, vendor_trans, amount_field,
                            vendor_limit, limit_field
                        )
                
                if len(exceeds) > 0:
                    st.error(f"⚠️ Found {len(exceeds)} creditors exceeding credit limits!")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Creditors Over Limit", len(exceeds))
                    with col2:
                        st.metric("Total Excess Amount", f"${exceeds['Excess_Amount'].sum():,.2f}")
                    with col3:
                        st.metric("Largest Excess", f"${exceeds['Excess_Amount'].max():,.2f}")
                    
                    st.dataframe(exceeds, use_container_width=True)
                    
                    csv = exceeds.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"{analysis_type.split()[0]}_Exceeds_Limit_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.success("✅ No creditors exceeding credit limits!")
    
    # ==================== AP06 - CREDITOR SUMMARY ====================
    elif analysis_type == "AP06 - Creditor Transaction Summary":
        st.header("📋 AP06: Creditor Transaction Summary")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            vendor_field = st.selectbox("Vendor Field", df.columns,
                                       index=df.columns.tolist().index(saved.get('vendor_field', df.columns[0])) if saved.get('vendor_field') in df.columns else 0)
            
            # Get unique vendors
            unique_vendors = sorted(df[vendor_field].unique())
            
            selected_vendor = st.selectbox(
                "Select Creditor",
                unique_vendors,
                index=unique_vendors.index(saved.get('selected_vendor', unique_vendors[0])) if saved.get('selected_vendor') in unique_vendors else 0
            )
            
            if st.button("🔍 Get Transactions", type="primary"):
                save_current_mapping(analysis_type, {
                    'vendor_field': vendor_field,
                    'selected_vendor': selected_vendor
                })
                
                with st.spinner("Extracting transactions..."):
                    result = ap06_creditor_summary(df, vendor_field, selected_vendor)
                
                if len(result) > 0:
                    st.success(f"✅ Found {len(result)} transactions for {selected_vendor}")
                    
                    # Summary stats
                    if len(result.select_dtypes(include=['number']).columns) > 0:
                        amount_col = result.select_dtypes(include=['number']).columns[0]
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Total Transactions", len(result))
                        with col2:
                            st.metric("Total Amount", f"${result[amount_col].sum():,.2f}")
                        with col3:
                            st.metric("Average Amount", f"${result[amount_col].mean():,.2f}")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Transactions",
                        data=csv,
                        file_name=f"AP06_{selected_vendor}_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("⚠️ No transactions found for this creditor")
    
    # ==================== AP07 - INVOICES WITHOUT PO ====================
    elif analysis_type == "AP07 - Invoices Without PO":
        st.header("📄 AP07: Invoices Without Purchase Orders")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            po_field = st.selectbox("Purchase Order Field", df.columns,
                                   index=df.columns.tolist().index(saved.get('po_field', df.columns[0])) if saved.get('po_field') in df.columns else 0)
            
            if st.button("🔍 Find Invoices Without PO", type="primary"):
                save_current_mapping(analysis_type, {'po_field': po_field})
                
                with st.spinner("Searching..."):
                    result = ap07_invoices_without_po(df, po_field)
                
                if len(result) > 0:
                    st.warning(f"⚠️ Found {len(result)} invoices without purchase orders ({len(result)/len(df)*100:.1f}% of total)")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"AP07_Without_PO_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.success("✅ All invoices have purchase orders!")
    
    # ==================== AP08 - AROUND DATE ====================
    elif analysis_type == "AP08 - Transactions Around Date":
        st.header("🎯 AP08: Transactions Around Specified Date")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                date_field = st.selectbox("Date Field", df.columns,
                                         index=df.columns.tolist().index(saved.get('date_field', df.columns[0])) if saved.get('date_field') in df.columns else 0)
            with col2:
                target_date = st.date_input("Target Date", value=datetime.now())
            with col3:
                days_range = st.number_input("Days Range (±)", min_value=1, value=saved.get('days_range', 7))
            
            if st.button("🔍 Find Transactions", type="primary"):
                save_current_mapping(analysis_type, {
                    'date_field': date_field,
                    'days_range': days_range
                })
                
                with st.spinner("Searching..."):
                    result = ap08_transactions_around_date(df, date_field, target_date, days_range)
                
                if len(result) > 0:
                    st.success(f"✅ Found {len(result)} transactions within ±{days_range} days of {target_date}")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"AP08_Around_{target_date}_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning(f"⚠️ No transactions found within ±{days_range} days of {target_date}")
    
    # ==================== AP09 - DATE RANGE ====================
    elif analysis_type == "AP09 - Transactions in Date Range":
        st.header("📅 AP09: Transactions Posted in Date Range")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                date_field = st.selectbox("Date Field", df.columns,
                                         index=df.columns.tolist().index(saved.get('date_field', df.columns[0])) if saved.get('date_field') in df.columns else 0)
            with col2:
                start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=30))
            with col3:
                end_date = st.date_input("End Date", value=datetime.now())
            
            if st.button("🔍 Find Transactions", type="primary"):
                save_current_mapping(analysis_type, {'date_field': date_field})
                
                with st.spinner("Searching..."):
                    result = ap09_transactions_date_range(df, date_field, start_date, end_date)
                
                if len(result) > 0:
                    st.success(f"✅ Found {len(result)} transactions between {start_date} and {end_date}")
                    
                    if len(result.select_dtypes(include=['number']).columns) > 0:
                        amount_col = result.select_dtypes(include=['number']).columns[0]
                        col1, col2 = st.columns(2)
                        with col1:
                            st.metric("Total Transactions", len(result))
                        with col2:
                            st.metric("Total Amount", f"${result[amount_col].sum():,.2f}")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"AP09_DateRange_{start_date}_to_{end_date}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning(f"⚠️ No transactions found in date range")
    
    # ==================== AP10 - TIME RANGE ====================
    elif analysis_type == "AP10 - Transactions by Time":
        st.header("⏰ AP10: Transactions Posted at Specific Times")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            datetime_field = st.selectbox("DateTime Field", df.columns,
                                         index=df.columns.tolist().index(saved.get('datetime_field', df.columns[0])) if saved.get('datetime_field') in df.columns else 0)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("Start Time")
                start_hour = st.selectbox("Hour", range(24), key='start_hour')
                start_minute = st.selectbox("Minute", range(60), key='start_min')
                start_second = st.selectbox("Second", range(60), key='start_sec')
                start_time = f"{start_hour:02d}:{start_minute:02d}:{start_second:02d}"
            
            with col2:
                st.subheader("End Time")
                end_hour = st.selectbox("Hour", range(24), key='end_hour', index=23)
                end_minute = st.selectbox("Minute", range(60), key='end_min', index=59)
                end_second = st.selectbox("Second", range(60), key='end_sec', index=59)
                end_time = f"{end_hour:02d}:{end_minute:02d}:{end_second:02d}"
            
            if st.button("🔍 Find Transactions", type="primary"):
                save_current_mapping(analysis_type, {'datetime_field': datetime_field})
                
                with st.spinner("Searching..."):
                    result = ap10_transactions_time_range(df, datetime_field, start_time, end_time)
                
                if len(result) > 0:
                    st.success(f"✅ Found {len(result)} transactions between {start_time} and {end_time}")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"AP10_TimeRange_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning(f"⚠️ No transactions found in time range")
    
    # ==================== AP11 - BY USERID ====================
    elif analysis_type == "AP11 - Transactions by UserID":
        st.header("👤 AP11: Transactions by UserID")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            user_field = st.selectbox("User Field", df.columns,
                                     index=df.columns.tolist().index(saved.get('user_field', df.columns[0])) if saved.get('user_field') in df.columns else 0)
            
            # Get unique users
            unique_users = sorted(df[user_field].dropna().unique())
            
            selected_user = st.selectbox(
                "Select User",
                unique_users,
                index=unique_users.index(saved.get('selected_user', unique_users[0])) if saved.get('selected_user') in unique_users else 0
            )
            
            if st.button("🔍 Get Transactions", type="primary"):
                save_current_mapping(analysis_type, {
                    'user_field': user_field,
                    'selected_user': selected_user
                })
                
                with st.spinner("Extracting transactions..."):
                    result = ap11_transactions_by_user(df, user_field, selected_user)
                
                if len(result) > 0:
                    st.success(f"✅ Found {len(result)} transactions by {selected_user}")
                    
                    if len(result.select_dtypes(include=['number']).columns) > 0:
                        amount_col = result.select_dtypes(include=['number']).columns[0]
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Total Transactions", len(result))
                        with col2:
                            st.metric("Total Amount", f"${result[amount_col].sum():,.2f}")
                        with col3:
                            st.metric("Average Amount", f"${result[amount_col].mean():,.2f}")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Transactions",
                        data=csv,
                        file_name=f"AP11_{selected_user}_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("⚠️ No transactions found for this user")
    
    # ==================== AP12 - WEEKENDS ====================
    elif analysis_type == "AP12 - Weekend Transactions":
        st.header("📅 AP12: Transactions Posted on Weekends")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            col1, col2 = st.columns(2)
            
            with col1:
                date_field = st.selectbox("Date Field", df.columns,
                                         index=df.columns.tolist().index(saved.get('date_field', df.columns[0])) if saved.get('date_field') in df.columns else 0)
            with col2:
                weekend_type = st.radio(
                    "Weekend Definition",
                    ["sat_sun", "sun_mon"],
                    format_func=lambda x: "Saturday & Sunday" if x == "sat_sun" else "Sunday & Monday"
                )
            
            if st.button("🔍 Find Weekend Transactions", type="primary"):
                save_current_mapping(analysis_type, {'date_field': date_field})
                
                with st.spinner("Searching..."):
                    result = ap12_weekend_transactions(df, date_field, weekend_type)
                
                if len(result) > 0:
                    st.warning(f"⚠️ Found {len(result)} transactions posted on weekends ({len(result)/len(df)*100:.1f}% of total)")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"AP12_Weekend_Transactions_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.success("✅ No weekend transactions found!")
    
    # ==================== AP13 - ROUNDED AMOUNTS ====================
    elif analysis_type == "AP13 - Rounded Amounts":
        st.header("🔢 AP13: Transactions With Rounded Amounts")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            amount_field = st.selectbox("Amount Field", df.select_dtypes(include=['number']).columns,
                                       index=df.select_dtypes(include=['number']).columns.tolist().index(saved.get('amount_field', df.select_dtypes(include=['number']).columns[0])) if saved.get('amount_field') in df.select_dtypes(include=['number']).columns else 0)
            
            st.info("ℹ️ This will find transactions with whole number amounts (no decimals), which may indicate manual entry or potential fraud.")
            
            if st.button("🔍 Find Rounded Amounts", type="primary"):
                save_current_mapping(analysis_type, {'amount_field': amount_field})
                
                with st.spinner("Analyzing..."):
                    result = ap13_rounded_amounts(df, amount_field)
                
                if len(result) > 0:
                    st.warning(f"⚠️ Found {len(result)} transactions with rounded amounts ({len(result)/len(df)*100:.1f}% of total)")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Rounded Transactions", len(result))
                    with col2:
                        st.metric("Total Amount", f"${result[amount_field].sum():,.2f}")
                    with col3:
                        st.metric("Average Amount", f"${result[amount_field].mean():,.2f}")
                    
                    st.dataframe(result, use_container_width=True)
                    
                    csv = result.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Results",
                        data=csv,
                        file_name=f"AP13_Rounded_Amounts_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.success("✅ No rounded amounts found!")
    
    # ==================== AP14 - DUPLICATE FIELDS ====================
    elif analysis_type == "AP14 - Duplicate Field Search":
        st.header("🔎 AP14: Duplicate Field Search")
        st.info("ℹ️ Search for duplicates based on up to 4 custom fields")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"✅ Loaded {len(df):,} records")
            
            with st.expander("👁️ Preview Data"):
                st.dataframe(df.head(10), use_container_width=True)
            
            st.subheader("Select Fields to Check for Duplicates")
            st.markdown("*Select 1-4 fields. Leave as '-- None --' if not needed.*")
            
            col1, col2, col3, col4 = st.columns(4)
            
            none_option = "-- None --"
            field_options = [none_option] + list(df.columns)
            
            with col1:
                key1 = st.selectbox("Key Field 1", field_options,
                                   index=field_options.index(saved.get('key1', none_option)) if saved.get('key1') in field_options else 0)
            with col2:
                key2 = st.selectbox("Key Field 2", field_options,
                                   index=field_options.index(saved.get('key2', none_option)) if saved.get('key2') in field_options else 0)
            with col3:
                key3 = st.selectbox("Key Field 3", field_options,
                                   index=field_options.index(saved.get('key3', none_option)) if saved.get('key3') in field_options else 0)
            with col4:
                key4 = st.selectbox("Key Field 4", field_options,
                                   index=field_options.index(saved.get('key4', none_option)) if saved.get('key4') in field_options else 0)
            
            error_limit = st.number_input("Maximum Results to Display", min_value=0, value=saved.get('error_limit', 20), 
                                         help="Set to 0 for no limit")
            
            # Filter out None selections
            selected_fields = [f for f in [key1, key2, key3, key4] if f != none_option]
            
            if len(selected_fields) > 0:
                st.info(f"📋 Will search for duplicates based on: {', '.join(selected_fields)}")
                
                if st.button("🔍 Find Duplicates", type="primary"):
                    save_current_mapping(analysis_type, {
                        'key1': key1,
                        'key2': key2,
                        'key3': key3,
                        'key4': key4,
                        'error_limit': error_limit
                    })
                    
                    with st.spinner("Searching for duplicates..."):
                        result = ap14_duplicate_fields(df, selected_fields, error_limit if error_limit > 0 else None)
                    
                    if len(result) > 0:
                        st.error(f"⚠️ Found {len(result)} duplicate records!")
                        
                        # Show duplicate counts by key combination
                        dup_counts = result.groupby(selected_fields).size().reset_index(name='Count')
                        dup_counts = dup_counts[dup_counts['Count'] > 1].sort_values('Count', ascending=False)
                        
                        st.subheader("Duplicate Summary")
                        st.dataframe(dup_counts, use_container_width=True)
                        
                        st.subheader("Detailed Results")
                        st.dataframe(result, use_container_width=True)
                        
                        csv = result.to_csv(index=False)
                        st.download_button(
                            label="📥 Download Duplicates",
                            data=csv,
                            file_name=f"AP14_Duplicates_{datetime.now().strftime('%Y%m%d')}.csv",
                            mime="text/csv"
                        )
                    else:
                        st.success("✅ No duplicates found!")
            else:
                st.warning("⚠️ Please select at least one field to search for duplicates")

if __name__ == "__main__":
    main()
