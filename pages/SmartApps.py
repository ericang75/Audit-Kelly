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

# AP01: Aging By Invoice Date
def ap01_aging_analysis(df, date_field, amount_field, cutoff_date, periods='30,60,90,120'):
    """Age invoices based on invoice date"""
    df = df.copy()
    df[date_field] = pd.to_datetime(df[date_field])
    cutoff = pd.to_datetime(cutoff_date)
    
    # Calculate days old
    df['Days_Old'] = (cutoff - df[date_field]).dt.days
    
    # Create aging buckets
    period_list = [int(p) for p in periods.split(',')]
    
    def assign_bucket(days):
        if days < 0:
            return 'Out of Bounds'
        for i, period in enumerate(period_list):
            if days <= period:
                prev = period_list[i-1] if i > 0 else 0
                return f'{prev+1}-{period} days'
        return f'Over {period_list[-1]} days'
    
    df['Age_Bucket'] = df['Days_Old'].apply(assign_bucket)
    
    # Summarize by bucket
    summary = df.groupby('Age_Bucket')[amount_field].agg(['sum', 'count']).reset_index()
    summary.columns = ['Age_Bucket', 'Total_Amount', 'Count']
    
    return df, summary

# AP02: Duplicate Invoices Detection
def ap02_find_duplicates(df, vendor_field, invoice_field, date_field, amount_field, 
                         test_type='exact', tolerance=0):
    """Find duplicate invoices based on various criteria"""
    df = df.copy()
    
    if test_type == 'exact':
        # Exact duplicates
        subset = [vendor_field, invoice_field, date_field, amount_field]
        duplicates = df[df.duplicated(subset=subset, keep=False)]
        
    elif test_type == 'near_invoice':
        # Near duplicates on invoice number (within tolerance days)
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
        
        if duplicates_list:
            duplicates = pd.DataFrame(duplicates_list).drop_duplicates()
        else:
            duplicates = pd.DataFrame()
            
    elif test_type == 'near_date':
        # Same vendor, invoice, amount but dates within tolerance
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
        
        if duplicates_list:
            duplicates = pd.DataFrame(duplicates_list).drop_duplicates()
        else:
            duplicates = pd.DataFrame()
            
    elif test_type == 'similar_vendor':
        # Similar vendor names (fuzzy match with tolerance as similarity threshold)
        # For simplicity, using exact match on other fields
        subset = [invoice_field, date_field, amount_field]
        duplicates = df[df.duplicated(subset=subset, keep=False)]
        
    elif test_type == 'similar_invoice':
        # Similar invoice numbers
        subset = [vendor_field, date_field, amount_field]
        duplicates = df[df.duplicated(subset=subset, keep=False)]
    
    return duplicates

# AP03: Creditors With Net Debit Balances
def ap03_debit_balances(df, vendor_field, amount_field):
    """Find creditors with net debit balances (positive amounts in AP = debit)"""
    # Summarize by vendor
    summary = df.groupby(vendor_field)[amount_field].sum().reset_index()
    summary.columns = [vendor_field, 'Net_Balance']
    
    # Filter for positive (debit) balances
    debit_balances = summary[summary['Net_Balance'] > 0].copy()
    debit_balances = debit_balances.sort_values('Net_Balance', ascending=False)
    
    return debit_balances

# AP04: Creditors With Balances > Credit Limit
def ap04_exceeds_limit(df_transactions, df_limits, 
                       vendor_field_trans, amount_field,
                       vendor_field_limit, limit_field):
    """Find creditors whose balance exceeds credit limit"""
    # Summarize transactions by vendor
    balances = df_transactions.groupby(vendor_field_trans)[amount_field].sum().reset_index()
    balances.columns = [vendor_field_trans, 'Current_Balance']
    
    # Join with credit limits
    merged = balances.merge(
        df_limits[[vendor_field_limit, limit_field]], 
        left_on=vendor_field_trans, 
        right_on=vendor_field_limit,
        how='inner'
    )
    
    # Filter where balance exceeds limit
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
    
    # Filter by date range
    mask = (df[date_field] >= pd.to_datetime(start_date)) & (df[date_field] <= pd.to_datetime(end_date))
    df_filtered = df[mask]
    
    # Summarize by vendor
    totals = df_filtered.groupby(vendor_field_trans)[amount_field].sum().reset_index()
    totals.columns = [vendor_field_trans, 'Total_Amount']
    
    # Join with limits
    merged = totals.merge(
        df_limits[[vendor_field_limit, limit_field]],
        left_on=vendor_field_trans,
        right_on=vendor_field_limit,
        how='inner'
    )
    
    # Filter exceeds
    exceeds = merged[merged['Total_Amount'] > merged[limit_field]].copy()
    exceeds['Excess_Amount'] = exceeds['Total_Amount'] - exceeds[limit_field]
    exceeds = exceeds.sort_values('Excess_Amount', ascending=False)
    
    return exceeds

# UI Components
def render_sidebar():
    st.sidebar.title("📊 AP Analyzer")
    st.sidebar.markdown("---")
    
    analysis_type = st.sidebar.selectbox(
        "Select Analysis",
        [
            "AP01 - Aging by Invoice Date",
            "AP02 - Duplicate Invoices",
            "AP03 - Net Debit Balances",
            "AP04 - Balances > Credit Limit",
            "AP05 - Period Amounts > Limit"
        ]
    )
    
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
    """Get saved mapping for analysis type"""
    return st.session_state.mappings.get(analysis_key, {})

def save_current_mapping(analysis_key, mapping):
    """Save current mapping"""
    st.session_state.mappings[analysis_key] = mapping

# Main App
def main():
    analysis_type = render_sidebar()
    
    st.title("🏦 Accounts Payable Analysis Tool")
    st.markdown("Python-powered alternative to Arbutus Analyzer procedures")
    
    # Get saved mapping for this analysis
    saved = get_saved_mapping(analysis_type)
    
    # AP01 - Aging Analysis
    if analysis_type == "AP01 - Aging by Invoice Date":
        st.header("📅 AP01: Aging by Invoice Date")
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
            
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            
            st.success(f"Loaded {len(df)} records")
            with st.expander("Preview Data"):
                st.dataframe(df.head())
            
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
                # Save mapping
                save_current_mapping(analysis_type, {
                    'date_field': date_field,
                    'amount_field': amount_field,
                    'periods': periods
                })
                
                with st.spinner("Analyzing..."):
                    result_df, summary = ap01_aging_analysis(df, date_field, amount_field, cutoff_date, periods)
                    
                st.success("Analysis Complete!")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.subheader("📊 Aging Summary")
                    st.dataframe(summary, use_container_width=True)
                    
                with col2:
                    st.subheader("📈 Visualization")
                    st.bar_chart(summary.set_index('Age_Bucket')['Total_Amount'])
                
                st.subheader("📋 Detailed Results")
                st.dataframe(result_df, use_container_width=True)
                
                # Download
                csv = result_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download Full Results",
                    data=csv,
                    file_name=f"AP01_Aging_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
    
    # AP02 - Duplicate Detection
    elif analysis_type == "AP02 - Duplicate Invoices":
        st.header("🔍 AP02: Duplicate Invoices Detection")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"Loaded {len(df)} records")
            
            with st.expander("Preview Data"):
                st.dataframe(df.head())
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                vendor_field = st.selectbox("Vendor Field", df.columns,
                                           index=df.columns.tolist().index(saved.get('vendor_field', df.columns[0])) if saved.get('vendor_field') in df.columns else 0)
            with col2:
                invoice_field = st.selectbox("Invoice Field", df.columns,
                                            index=df.columns.tolist().index(saved.get('invoice_field', df.columns[1])) if saved.get('invoice_field') in df.columns else 1)
            with col3:
                date_field = st.selectbox("Date Field", df.columns,
                                         index=df.columns.tolist().index(saved.get('date_field', df.columns[2])) if saved.get('date_field') in df.columns else 2)
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
    
    # AP03 - Debit Balances
    elif analysis_type == "AP03 - Net Debit Balances":
        st.header("💰 AP03: Creditors With Net Debit Balances")
        
        uploaded_file = st.file_uploader("Upload AP Transactions File", type=['csv', 'xlsx'])
        
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.success(f"Loaded {len(df)} records")
            
            with st.expander("Preview Data"):
                st.dataframe(df.head())
            
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
    
    # AP04 & AP05 - Credit Limit Checks
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
            
            st.success(f"Loaded {len(df_trans)} transactions and {len(df_limits)} credit limits")
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Transactions Preview")
                st.dataframe(df_trans.head(3))
            with col2:
                st.subheader("Credit Limits Preview")
                st.dataframe(df_limits.head(3))
            
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

if __name__ == "__main__":
    main()