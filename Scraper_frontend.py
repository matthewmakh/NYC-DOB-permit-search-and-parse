import streamlit as st
from datetime import datetime
from dateutil.relativedelta import relativedelta
import mysql.connector
import permit_scraper
import os
from dotenv import load_dotenv

load_dotenv()

#
# Setup Database
conn = mysql.connector.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    user=os.getenv('DB_USER', 'scraper_user'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME', 'permit_scraper')
)
cursor = conn.cursor()


month_days = {
    1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31
}

permit_types = ["AL", "DM", "EQ", "EW", "FO", "NB", "PL", "SG"]

permit_type_dict = {
    "AL": "AL - ALTERATION",
    "DM": "DM - DEMOLITION & REMOVAL",
    "EQ": "EQ - CONSTRUCTION EQUIPMENT",
    "EW": "EW - EQUIPMENT WORK",
    "FO": "FO - FOUNDATION/EARTHWORK",
    "NB": "NB - NEW BUILDING",
    "PL": "PL - PLUMBING",
    "SG": "SG - SIGN"
}

def is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


#Start Streamlit

st.title("DOB Scraper")
st.subheader("Enter the Date Range and Permit Type")



# 📆 Default dates
today = datetime.now()
one_month_ago = today - relativedelta(months=1)

selected_label = st.selectbox("Permit Type", list(permit_type_dict.values()), index=permit_types.index("NB"))
selected_code = [k for k, v in permit_type_dict.items() if v == selected_label][0]


with st.form("Date Form"):
    # Streamlit columns
    col1, col2 = st.columns(2)

    # -------- FROM DATE (1 month ago) --------
    with col1:
        st.markdown("### From Date")
        from_year = int(st.number_input("Year", value=one_month_ago.year, step=1, key="from_year"))

        month_keys = list(month_days.keys())
        from_month_index = month_keys.index(one_month_ago.month)
        from_month = st.selectbox("Month", month_keys, index=from_month_index, key="from_month")

        max_day_from = 29 if from_month == 2 and is_leap_year(from_year) else month_days[from_month]
        from_day_options = list(range(1, max_day_from + 1))
        default_from_day = one_month_ago.day if one_month_ago.day <= max_day_from else max_day_from
        from_day_index = from_day_options.index(default_from_day)
        from_day = st.selectbox("Day", from_day_options, index=from_day_index, key="from_day")

    # -------- TO DATE (Today) --------
    with col2:
        st.markdown("""### To Date (Unavailable)""")
        to_year = int(st.number_input("Year", value=today.year, step=1, key="to_year"))

        to_month_index = month_keys.index(today.month)
        to_month = st.selectbox("Month", month_keys, index=to_month_index, key="to_month")

        max_day_to = 29 if to_month == 2 and is_leap_year(to_year) else month_days[to_month]
        to_day_options = list(range(1, max_day_to + 1))
        default_to_day = today.day if today.day <= max_day_to else max_day_to
        to_day_index = to_day_options.index(default_to_day)
        to_day = st.selectbox("Day", to_day_options, index=to_day_index, key="to_day")
        st.warning("Feature Coming Soon!")

    # -------- OUTPUT --------
    st.markdown("---")
    submitted = st.form_submit_button("Submit")

    if submitted:
        cursor.execute("""
            INSERT INTO permit_search_config (
                start_month, start_day, start_year,
                end_month, end_day, end_year,
                permit_type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (from_month, from_day, from_year, to_month, to_day, to_year, selected_code))

        conn.commit()

        cursor.execute("""
        SELECT * FROM permit_search_config
        ORDER BY created_at DESC
        LIMIT 1;
        """)

        config = cursor.fetchone()

        start_month = config[1]
        start_day = config[2]
        start_year = config[3]
        permit_type = config[4]

        st.warning(f"Submitted: {start_month}/{start_day}/{start_year}, Permit Type: {permit_type}")



