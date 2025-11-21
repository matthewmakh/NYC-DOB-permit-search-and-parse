#!/usr/bin/env python3
"""
Fix all contacts table references in app.py to use permits table columns instead
"""

import re

# Read the file
with open('dashboard_html/app.py', 'r') as f:
    content = f.read()

# Pattern 1: Contact info subquery in /api/permits (line ~120)
# Replace contacts table aggregation with permits table column check
old_pattern_1 = r"""LEFT JOIN \(
                SELECT 
                    permit_id,
                    COUNT\(\*\) as contact_count,
                    BOOL_OR\(COALESCE\(is_mobile, false\)\) as has_mobile,
                    STRING_AGG\(name, '\|' ORDER BY name\) as contact_names,
                    STRING_AGG\(phone, '\|' ORDER BY name\) as contact_phones
                FROM contacts
                WHERE name IS NOT NULL AND name != ''
                GROUP BY permit_id
            \) contact_info ON p\.id = contact_info\.permit_id"""

new_pattern_1 = """-- Contact info now comes from permits table columns
            -- contact_count, has_mobile, contact_names, contact_phones calculated from permit fields"""

content = re.sub(old_pattern_1, new_pattern_1, content, flags=re.MULTILINE | re.DOTALL)

# Pattern 2: Stats endpoint contact counts (lines ~164, 168)
# Just comment them out or set to 0
old_pattern_2a = r'cur\.execute\("SELECT COUNT\(\*\) as total FROM contacts WHERE name IS NOT NULL AND name != \'\';"\)'
new_pattern_2a = '# Legacy: contacts table deprecated\n        total_contacts = 0'

old_pattern_2b = r'cur\.execute\("SELECT COUNT\(\*\) as total FROM contacts WHERE is_mobile = TRUE;"\)'
new_pattern_2b = '# Legacy: contacts table deprecated\n        mobile_contacts = 0'

content = re.sub(old_pattern_2a, new_pattern_2a, content)
content = re.sub(old_pattern_2b, new_pattern_2b, content)

# Pattern 3: get_permit_details subquery (~line 468)
old_pattern_3 = r"""SELECT 
                    permit_id,
                    STRING_AGG\(DISTINCT name, ', '\) as contact_names,
                    STRING_AGG\(DISTINCT phone, ', '\) as contact_phones
                FROM contacts 
                WHERE permit_id = %s
                GROUP BY permit_id"""

new_pattern_3 = """-- Contacts now in permits table columns
                SELECT 
                    id as permit_id,
                    CONCAT_WS(', ',
                        NULLIF(permittee_business_name, ''),
                        NULLIF(owner_business_name, ''), 
                        NULLIF(superintendent_business_name, '')
                    ) as contact_names,
                    CONCAT_WS(', ',
                        NULLIF(permittee_phone, ''),
                        NULLIF(owner_phone, '')
                    ) as contact_phones
                FROM permits
                WHERE id = %s"""

content = re.sub(old_pattern_3, new_pattern_3, content, flags=re.MULTILINE | re.DOTALL)

# Pattern 4: permit_detail page query (~line 559, 578)
# These appear to be similar contact aggregations - replace with permits table
old_pattern_4 = r"""SELECT 
                    name, phone
                FROM contacts
                WHERE permit_id = %s"""

new_pattern_4 = """-- Contacts from permits table
                SELECT 
                    UNNEST(ARRAY[permittee_business_name, owner_business_name, superintendent_business_name]) as name,
                    UNNEST(ARRAY[permittee_phone, owner_phone, NULL]) as phone
                FROM permits
                WHERE id = %s
                    AND (permittee_business_name IS NOT NULL OR owner_business_name IS NOT NULL OR superintendent_business_name IS NOT NULL)"""

content = re.sub(old_pattern_4, new_pattern_4, content, flags=re.MULTILINE | re.DOTALL)

# Pattern 5: LEFT JOIN contacts patterns (lines ~596, 681)
old_pattern_5 = r'LEFT JOIN contacts c ON p\.id = c\.permit_id'
new_pattern_5 = '-- Contact info from permits table columns (no JOIN needed)'

content = re.sub(old_pattern_5, new_pattern_5, content)

# Pattern 6: Building contacts queries (~lines 691, 739, 1378, 1453)
old_pattern_6 = r"""SELECT c\.name, c\.phone
            FROM contacts c
            INNER JOIN permits p ON c\.permit_id = p\.id
            WHERE p\.bbl = %s"""

new_pattern_6 = """-- Get contacts from permits table for this building
            SELECT 
                UNNEST(ARRAY[p.permittee_business_name, p.owner_business_name, p.superintendent_business_name]) as name,
                UNNEST(ARRAY[p.permittee_phone, p.owner_phone, NULL]) as phone
            FROM permits p
            WHERE p.bbl = %s
                AND (p.permittee_business_name IS NOT NULL OR p.owner_business_name IS NOT NULL OR p.superintendent_business_name IS NOT NULL)"""

content = re.sub(old_pattern_6, new_pattern_6, content, flags=re.MULTILINE | re.DOTALL)

# Pattern 7: INNER JOIN contacts (~lines 1193, 1313)  
old_pattern_7 = r'INNER JOIN contacts c ON p\.id = c\.permit_id'
new_pattern_7 = '-- Contact info from permits table columns'

content = re.sub(old_pattern_7, new_pattern_7, content)

print(f"✅ Fixed all contacts table references")
print(f"   Replaced contacts JOIN/subqueries with permits table columns")
print(f"   Total file length: {len(content.splitlines())} lines")

# Write back
with open('dashboard_html/app.py', 'w') as f:
    f.write(content)

print("✅ File updated successfully")
